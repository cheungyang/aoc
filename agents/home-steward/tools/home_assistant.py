"""The single Home Assistant tool.

One tool, many actions, rather than one tool per capability. Two reasons:

  * Token cost. One tool per capability would inject ~20 schemas into every
    prompt of every agent holding the grant, on every message. One schema is a
    fraction of that, and being static it sits in the cacheable prefix of the
    system prompt.
  * Stability. The action vocabulary here is ours and does not move with the
    Home Assistant version.

Which transport (REST or WebSocket) serves an action is decided by
`routing.py`, never by the model.

Reads apply immediately. Mutating actions are shape-checked here, then run the
guard chain in `guards.py`, and config writes are snapshotted and verified by
`writes.py` so a bad change can be undone. `upsert_helper` is declared but not
yet dispatched (it is served by the WebSocket API), so it is refused at the
shape check, before a confirmation could be issued for it.

Each instruction stands alone. What it produced (a result, or a confirmation
request) goes in the envelope's payload; a refusal or failure is an issue
(`partial`), and the rest of the batch still runs. A call where nothing could
be done is `failed`. The rules in full, with examples: agents/home-steward/WORKFLOW.md.
"""
import fnmatch
import json

from agents.home_steward.lib.homeassistant import error_log as error_log_mod
from agents.home_steward.lib.homeassistant import guards
from agents.home_steward.lib.homeassistant import inventory as inventory_mod
from agents.home_steward.lib.homeassistant import writes as writes_mod
from agents.home_steward.lib.homeassistant.client import HomeAssistantClient, HomeAssistantError
from agents.home_steward.lib.homeassistant.routing import READ_ACTIONS, WRITE_ACTIONS
from agents.home_steward.lib.homeassistant.ws import HomeAssistantWebSocket
from core.tooling import Report, ToolError, agent_tool, current_agent_id, has_permission, tool_grant

# Actions this tool can actually perform. Kept separate from routing.READ_ACTIONS
# so that an action which is declared and routed but not yet dispatched produces
# a clear "not available" message instead of an obscure failure.
IMPLEMENTED = frozenset({
    "inventory",
    "search_registry",
    "get_state",
    "list_entities",
    "get_config",
    "list_services",
    "render_template",
    "history",
    "logbook",
    "error_log",
    "check_config",
    "list_automations",
    "get_automation",
})

# The same distinction on the write side. `upsert_helper` is declared in the
# routing table, but is served by the WebSocket API with a different schema per
# helper domain, so it is not dispatched yet and is refused by `_validate_write`.
WRITE_IMPLEMENTED = frozenset({
    "call_service",
    "upsert_automation",
    "upsert_script",
    "upsert_scene",
    "delete_automation",
    "delete_script",
    "delete_scene",
    "reload",
})

# The keys each dispatched write accepts besides "action" and "confirm_token".
# Anything else is refused before a confirmation is issued. A misnamed key used
# to cost an approval: an automation sent under "automation" instead of "config"
# was shown to the user, approved, and only then failed for want of "config".
_WRITE_KEYS = {
    "call_service": frozenset({"domain", "service", "entity_id", "target", "service_data", "data"}),
    "upsert_automation": frozenset({"config", "id"}),
    "upsert_script": frozenset({"config", "id"}),
    "upsert_scene": frozenset({"config", "id"}),
    "delete_automation": frozenset({"id"}),
    "delete_script": frozenset({"id"}),
    "delete_scene": frozenset({"id"}),
    "reload": frozenset({"domain"}),
}

# Top-level config keys Home Assistant will not store a config without. Each
# entry lists the accepted spellings: HA 2024.10 renamed trigger/action to
# triggers/actions and reads both. Everything deeper is left to HA, which
# validates the config when it is applied.
_REQUIRED_CONFIG_KEYS = {
    "upsert_automation": (("triggers", "trigger"), ("actions", "action")),
    "upsert_script": (("sequence",),),
    "upsert_scene": (("entities",),),
}

# Named action bundles for `agent.json` grants, e.g. {"light.*": ["@control"]}.
# Declared here, beside the action vocabulary, so adding an action and deciding
# which bundle it belongs to are the same edit. `ToolsLoader` resolves these
# during permission merge; see core/permissions/bundles.py.
PERMISSION_BUNDLES = {
    # Every read action. Safe to run unattended, so this is the grant most
    # agents should have and nothing more.
    "@observe": sorted(READ_ACTIONS),
    # Actuating existing things. Deliberately does *not* include @observe: the
    # two are granted against different selectors (observe on "*", control on
    # "light.*"), so folding one into the other would silently widen the narrow
    # grant to everything the broad one covers.
    "@control": ["call_service"],
    # Creating and changing stored config. Everything here requires human
    # confirmation at the guard layer regardless of the grant.
    "@author": [
        "upsert_automation", "upsert_script", "upsert_scene",
        "upsert_helper", "reload",
    ],
    # Authoring plus destruction.
    "@admin": ["@author", "delete_automation", "delete_script", "delete_scene"],
}


def PERMISSION_MATCHER(selector: str, target: str) -> bool:
    """Whether a grant selector covers the entity this instruction touches.

    Home Assistant targets are entity ids (`light.porch`), domains (`light`), or
    `"*"` when an instruction names neither. None of those are filesystem paths,
    and the loader's default matcher treats them as such -- which made
    `{"light.*": [...]}` match nothing and `{"*": [...]}` match only instructions
    with no entity at all. Both measured. See core/permissions/matchers.py.

    Globs, because that is what the selectors already look like:

        "*"          -> everything
        "light.*"    -> every entity in the light domain
        "light.porch"-> exactly that entity

    A bare domain selector also covers its entities, so `{"light": [...]}` and
    `{"light.*": [...]}` agree. Without that, `{"light": ["call_service"]}` would
    permit only the literal target `"light"` -- which the tool passes when an
    instruction gives `domain` but no `entity_id` -- and deny every actual light.
    Two spellings of the same intent silently meaning different things is how a
    grant gets written once, tested once, and misread forever after.
    """
    if selector == target:
        return True
    if fnmatch.fnmatchcase(target, selector):
        return True
    # Domain selector covering entities in that domain.
    if "." not in selector and target.startswith(f"{selector}."):
        return True
    # Domain glob covering bare domain, e.g. "automation.*" covering "automation".
    return selector.endswith(".*") and selector[:-2] == target


def _target_of(instruction: dict) -> str:
    """Resolves the entity selector or domain target for permission checks and guards."""
    if instruction.get("entity_id"):
        return str(instruction["entity_id"])
    if isinstance(instruction.get("target"), dict) and instruction["target"].get("entity_id"):
        eid = instruction["target"]["entity_id"]
        return str(eid[0]) if isinstance(eid, list) and eid else str(eid)
    if instruction.get("domain"):
        return str(instruction["domain"])

    action = instruction.get("action")
    if action in ("upsert_automation", "delete_automation", "list_automations", "get_automation"):
        obj_id = instruction.get("id") or instruction.get("automation_id")
        return f"automation.{obj_id}" if obj_id else "automation"
    if action in ("upsert_script", "delete_script"):
        obj_id = instruction.get("id") or instruction.get("script_id")
        return f"script.{obj_id}" if obj_id else "script"
    if action in ("upsert_scene", "delete_scene"):
        obj_id = instruction.get("id") or instruction.get("scene_id")
        return f"scene.{obj_id}" if obj_id else "scene"
    if action == "upsert_helper":
        return "helper"

    return "*"


def _label(number: int, action, target: str) -> str:
    """How an issue names its instruction: the action and the entity or domain it touches.

    `number` (1-based) stands in when the instruction has no usable action.
    """
    if not isinstance(action, str) or not action:
        return f"instruction {number}"
    return action if target == "*" else f"{action} {target}"


def _service_entity(instruction: dict):
    """The one entity a call_service instruction names, or None.

    Read from `entity_id`, or from Home Assistant's own service-call shape,
    `target: {"entity_id": ...}`, which is what a model writes after reading
    automations. `_validate_write` has already refused lists of several.
    """
    entity_id = instruction.get("entity_id")
    if not entity_id and isinstance(instruction.get("target"), dict):
        entity_id = instruction["target"].get("entity_id")
    if isinstance(entity_id, list):
        entity_id = entity_id[0] if entity_id else None
    return str(entity_id) if entity_id else None


def _validate_write(action: str, instruction: dict):
    """Checks a write's shape. Returns a message for the agent, or None if well formed.

    Runs before the permission check and the guards, so a malformed write fails
    while the agent can still fix it -- never after the user has approved it.
    Only structure is checked here; Home Assistant validates a config's content
    when it is applied.
    """
    if action not in WRITE_IMPLEMENTED:
        return (
            f"'{action}' is not dispatched yet (it is served by the WebSocket API and "
            f"lands with helper support)."
        )

    accepted = _WRITE_KEYS[action]
    unexpected = sorted(set(instruction) - accepted - {"action", "confirm_token"})
    if unexpected:
        hint = " Put the body of the object under 'config'." if "config" in accepted else ""
        return (
            f"{action} does not accept {', '.join(repr(k) for k in unexpected)}; "
            f"it takes {', '.join(repr(k) for k in sorted(accepted))}.{hint}"
        )

    if action == "call_service":
        return _validate_call_service(instruction)

    has_id = bool(str(instruction.get("id") or "").strip())
    if action in _REQUIRED_CONFIG_KEYS:
        config = instruction.get("config")
        if not isinstance(config, dict) or not config:
            return f"{action} requires a non-empty 'config' object."
        missing = [
            repr(names[0]) + (f" (or {names[1]!r})" if len(names) > 1 else "")
            for names in _REQUIRED_CONFIG_KEYS[action]
            if not any(name in config for name in names)
        ]
        if missing:
            return f"{action} 'config' is missing {' and '.join(missing)}."
        if action == "upsert_script" and not has_id:
            return (
                "upsert_script requires 'id': it becomes the entity id (script.<id>), so "
                "it must be chosen rather than generated."
            )
    elif action.startswith("delete_") and not has_id:
        return f"{action} requires 'id' -- the object to delete."
    return None


def _validate_call_service(instruction: dict):
    """call_service's half of `_validate_write`: one entity, named consistently."""
    if not instruction.get("service"):
        return "call_service requires 'service' (e.g. 'turn_on')."

    target = instruction.get("target")
    if target is not None:
        # Area, device and label targets would act on entities that no permission
        # check or guard ever saw, so only an explicit entity is accepted.
        if not isinstance(target, dict) or set(target) != {"entity_id"}:
            return (
                "call_service accepts only target.entity_id; area, device and label "
                "targets are not supported. Find the entity ids with search_registry."
            )
        if instruction.get("entity_id"):
            return "Name the entity once: 'entity_id' or target.entity_id, not both."

    raw = instruction.get("entity_id") or (target or {}).get("entity_id")
    if isinstance(instruction.get("entity_id"), list) or (isinstance(raw, list) and len(raw) > 1):
        return (
            "call_service takes one entity per instruction. Send one instruction per "
            "entity; they can all go in the same call."
        )

    entity_id = _service_entity(instruction)
    domain = instruction.get("domain")
    if not entity_id and not domain:
        return (
            "call_service requires 'entity_id', or 'domain' for a service that "
            "targets no entity."
        )
    # Permissions and guards judge the entity, but the request goes to `domain`.
    # Letting them differ would let an allowed light carry `script.<name>` past
    # both, without the confirmation a script needs.
    if entity_id and domain and guards.domain_of(entity_id) != domain:
        return (
            f"'domain' {domain!r} does not match the entity {entity_id!r}. Omit "
            f"'domain' (it is read from the entity) or call the entity's own domain."
        )
    return None


@agent_tool
def home_assistant(report: Report, instructions: list[dict]) -> str:
    """Read and control Home Assistant: entities, areas, devices, automations, state.

    Reads: Home Assistant itself: states, registries, services, config, history, logbook, error log.
    Computes: the inventory and registry search; for each write, whether it applies at once, needs the user's confirmation, or is refused (below).
    Leaves out: which entity the user means, and whether a change is wise.

    Supports several actions in one call. Each instruction is a dict with an
    "action" key plus that action's arguments. Each one stands alone: one that is
    refused or fails is listed under <issues> with the reason, and the rest run.

    Start with `inventory` to orient yourself, then `search_registry` to find
    specific entities; an entity whose id you already have needs neither. Avoid
    `list_entities` without filters: this home has ~1400 entities and the
    unfiltered list is not useful.

    Actions:
      {"action": "inventory"}
          The whole home as an area x domain summary with totals. Fixed size.
      {"action": "search_registry", "query": "porch", "domain": "light",
       "area": "Garage", "include_disabled": false, "limit": 40}
          Filtered entity lookup. Reports the pre-cap total when truncated.
          All filters optional, but pass at least one.
      {"action": "get_state", "entity_id": "light.porch"}
          Current state and attributes of one entity.
      {"action": "list_entities", "domain": "light"}
          Entity ids and states from /api/states. `domain` strongly recommended.
      {"action": "get_config"}
          Core config: version, location, timezone, unit system.
      {"action": "list_services", "domain": "light"}
          Callable services, optionally filtered to one domain.
      {"action": "render_template", "template": "{{ states('light.porch') }}"}
          Evaluates a Jinja template. Read-only.
      {"action": "history", "entity_id": "light.porch", "hours": 24}
          State changes over a window.
      {"action": "logbook", "hours": 6}
          Human-readable event log.
      {"action": "error_log", "limit": 30, "max_chars": 20000}
          Home Assistant's error log, useful after a change. Repeated entries
          are collapsed with a count; newest first. Both fields optional.
      {"action": "check_config"}
          Validates the running configuration.
      {"action": "list_automations"} / {"action": "get_automation", "id": "<id>"}
          Existing automations, and one automation's full config.

    Writing:
      {"action": "call_service", "entity_id": "light.porch", "service": "turn_on",
       "service_data": {"brightness": 180}}
          Calls a service on one entity; send one instruction per entity. Home
          Assistant's {"target": {"entity_id": ...}} form is accepted too.
          Reversible domains (light, switch, fan, media_player, scene, climate
          setpoints within 12-28 °C, ...) apply immediately. Pass "domain"
          instead of "entity_id" for services that target no entity.
      {"action": "upsert_automation", "config": {...}, "id": "<optional>"}
          Creates an automation, or replaces it when "id" matches an existing one.
          Omit "id" to create; one is generated and returned. "config" is the
          automation itself: alias, description, triggers, conditions, actions, mode.
      {"action": "upsert_script", "id": "goodnight", "config": {...}}
          "id" is required here: it becomes the entity id (script.goodnight).
      {"action": "upsert_scene", "config": {...}, "id": "<optional>"}
      {"action": "delete_automation" | "delete_script" | "delete_scene", "id": "<id>"}
      {"action": "reload", "domain": "automation"}
          Reloads YAML config without restarting. Defaults to reloading everything.

    Confirmation protocol. Config writes, scripts and automation triggers return
    <confirmation_required> with a confirm_token instead of applying. Show the
    proposed change to the user, get explicit approval, then repeat the *identical*
    instruction with "confirm_token" added. Changing the payload invalidates the
    token, so do not "improve" it between proposing and confirming.

    Every config write snapshots the previous version to disk first and verifies
    the result by reading it back; a write that does not verify is rolled back
    automatically. The lock, alarm_control_panel, cover, garage_door, valve and
    water_heater domains are refused outright, whether called directly or named
    inside a config, and cannot be granted.

    Args:
        instructions: List of action dicts, executed in order.

    Returns:
        In <payload>, one <instruction_result> or <confirmation_required> per
        instruction carried out; under <issues>, each instruction refused or failed,
        and why (status partial, or failed when none was carried out).
    """
    if not isinstance(instructions, list) or not instructions:
        raise ToolError("'instructions' must be a non-empty list of action dicts.")

    # One element per instruction carried out: a result, or a confirmation request.
    payload_elements = []
    failures = []

    def failed(label: str, problem: str) -> None:
        """One instruction refused or failed: an issue, and the batch carries on."""
        failures.append(label)
        report.problem(label, problem)

    # Registry data is expensive to fetch and several actions need it, so it is
    # loaded at most once per call and shared across the batch.
    registry_cache = {}
    rest_client = None

    for number, instruction in enumerate(instructions, 1):
        if not isinstance(instruction, dict):
            failed(f"instruction {number}", f"Each instruction must be a dict, got {type(instruction).__name__}.")
            continue

        action = instruction.get("action")

        # The permission target is the entity selector the action touches, which
        # keeps grants readable as {"light.*": [...]} in agent.json.
        target = _target_of(instruction)
        label = _label(number, action, target)

        if action in WRITE_ACTIONS:
            # Shape first: a malformed write must fail before a confirmation token
            # is issued, so the user is never asked to approve a change that then
            # cannot be applied.
            problem = _validate_write(action, instruction)
            if problem:
                failed(label, problem)
                continue

            if not has_permission(action, target):
                failed(label, f"Agent {current_agent_id()} lacks permission for '{action}' on {target}.")
                continue

            element, problem = _guard_write(instruction, action, target)
            # Classify on which of the two came back, not on one specific success
            # shape. `_guard_write` returns a confirmation request or a successful
            # result as the element, or why it failed; testing for "is it a
            # confirmation?" filed every applied write under errors once writes
            # could actually succeed.
            if problem:
                failed(label, problem)
            else:
                payload_elements.append(element)
            continue

        if action not in IMPLEMENTED:
            known = ", ".join(sorted(IMPLEMENTED))
            failed(label, f"Unknown action. Available: {known}")
            continue

        if not has_permission(action, target):
            failed(label, f"Agent {current_agent_id()} lacks permission for '{action}' on {target}.")
            continue

        try:
            if rest_client is None:
                rest_client = HomeAssistantClient()
            result = _dispatch(action, instruction, rest_client, registry_cache)
            payload_elements.append(
                f'<instruction_result action="{action}">{_render(result)}</instruction_result>'
            )
        except HomeAssistantError as exc:
            failed(label, str(exc))
        except Exception as exc:  # noqa: BLE001 - surfaced to the agent, not swallowed
            failed(label, f"{type(exc).__name__}: {exc}")

    if failures and not payload_elements:
        raise ToolError("Nothing was done; the issues say why.")
    return "\n".join(payload_elements)


def _guard_write(instruction, action, target):
    """Runs the guard chain for one mutating action, then dispatches it.

    Returns (element, None): a <confirmation_required> when a human is needed, or
    an <instruction_result> when it was applied and verified. Returns
    (None, why) when the action is refused or failed.

    Dispatch lives behind the guards rather than beside them, so there is no path
    to a mutating call that skips authorisation -- the only way to reach the write
    is to fall off the end of `guards.authorise` without it raising.
    """
    # Everything except the bookkeeping keys is what the human is approving, and
    # therefore what the confirmation token is bound to.
    payload = {k: v for k, v in instruction.items() if k not in ("action", "confirm_token")}
    agent_id = current_agent_id()

    try:
        guards.authorise(
            action=action,
            target=target if target != "*" else None,
            payload=payload,
            confirm_token=instruction.get("confirm_token"),
            agent_id=agent_id,
            blanket_grant=guards.is_blanket_grant(tool_grant()),
        )
    except guards.ConfirmationRequired as need:
        return (
            f'<confirmation_required action="{action}" target="{target}">\n'
            f'  <proposed>{_render(need.payload)}</proposed>\n'
            f'  <confirm_token>{need.token}</confirm_token>\n'
            f'  <expires_in>{guards.CONFIRMATION_TTL_SECONDS}s</expires_in>\n'
            f'  <note>Show this to the user and get explicit approval. To apply, repeat '
            f'the same instruction unchanged with "confirm_token" added. Altering the '
            f'payload invalidates the token.</note>\n'
            f'</confirmation_required>'
        ), None
    except guards.GuardRejection as rejection:
        return None, str(rejection)

    try:
        result = _dispatch_write(action, instruction, payload, agent_id)
    except HomeAssistantError as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent, not swallowed
        return None, f"{type(exc).__name__}: {exc}"

    # A WriteResult that was applied but not verified has been rolled back (or
    # failed to roll back). Either way it is not a success, so it is reported as
    # an error -- but with the full result attached, because the snapshot path and
    # the rollback outcome are exactly what the agent needs to relay.
    if isinstance(result, writes_mod.WriteResult) and not result.verified:
        return None, _render(result.as_dict())

    rendered = result.as_dict() if isinstance(result, writes_mod.WriteResult) else result
    return f'<instruction_result action="{action}">{_render(rendered)}</instruction_result>', None


def _dispatch_write(action, instruction, payload, agent_id):
    """Performs one authorised mutating action."""
    rest = HomeAssistantClient()

    if action == "call_service":
        domain = instruction.get("domain")
        service = instruction.get("service")
        entity_id = _service_entity(instruction)

        # A bare entity_id carries the domain, so accept either form rather than
        # making the agent repeat itself.
        if not domain and entity_id:
            domain = guards.domain_of(entity_id)
        if not domain or not service:
            raise HomeAssistantError(
                "call_service requires 'service' plus either 'domain' or 'entity_id' "
                "(e.g. {'action':'call_service','entity_id':'light.porch','service':'turn_on'})."
            )
        return writes_mod.call_service(
            rest,
            domain=domain,
            service=service,
            entity_id=entity_id,
            service_data=instruction.get("service_data") or instruction.get("data"),
        )

    if action == "reload":
        return writes_mod.reload(rest, domain=instruction.get("domain") or "homeassistant")

    return writes_mod.apply_config_write(rest, action, payload, agent_id=agent_id)


def _render(result) -> str:
    """JSON for structures, plain text for text (error_log, templates)."""
    if isinstance(result, str):
        return result
    return json.dumps(result, indent=2, default=str)


def _registry_rows(cache: dict, rest_client: HomeAssistantClient):
    """Loads and joins the registries once per tool call."""
    if "rows" not in cache:
        with HomeAssistantWebSocket() as ws:
            entities = ws.entities()
            devices = ws.devices()
            areas = ws.areas()
        states = rest_client.get("/api/states")
        cache["rows"] = inventory_mod.build(entities, devices, areas, states)
    return cache["rows"]


def _dispatch(action, instruction, rest, cache):
    if action == "inventory":
        return inventory_mod.summarise(_registry_rows(cache, rest))

    if action == "search_registry":
        return inventory_mod.search(
            _registry_rows(cache, rest),
            query=instruction.get("query"),
            domain=instruction.get("domain"),
            area=instruction.get("area"),
            include_disabled=bool(instruction.get("include_disabled", False)),
            limit=int(instruction.get("limit", inventory_mod.DEFAULT_LIMIT)),
        )

    if action == "get_state":
        entity_id = instruction.get("entity_id")
        if not entity_id:
            raise HomeAssistantError("get_state requires 'entity_id'.")
        return rest.get(f"/api/states/{entity_id}")

    if action == "list_entities":
        domain = instruction.get("domain")
        states = rest.get("/api/states")
        rows = [
            {"entity_id": s.get("entity_id"), "state": s.get("state")}
            for s in states
            if not domain or str(s.get("entity_id", "")).startswith(f"{domain}.")
        ]
        limit = int(instruction.get("limit", inventory_mod.DEFAULT_LIMIT))
        return {
            "total": len(rows),
            "returned": min(len(rows), limit),
            "truncated": len(rows) > limit,
            "entities": rows[:limit],
        }

    if action == "get_config":
        config = rest.get("/api/config")
        if instruction.get("full"):
            return config
        # The raw payload is dominated by `components` (several hundred entries)
        # and the allowlist paths, none of which inform a decision. Keep the
        # fields an agent reasons about and report the rest by count.
        summary = {
            key: config.get(key)
            for key in (
                "version", "location_name", "time_zone", "currency", "country",
                "language", "unit_system", "state", "config_dir",
            )
            if config.get(key) is not None
        }
        summary["components_count"] = len(config.get("components") or [])
        summary["_note"] = "Pass {'action': 'get_config', 'full': true} for the raw payload."
        return summary

    if action == "list_services":
        domain = instruction.get("domain")
        services = rest.get("/api/services")
        if domain:
            services = [s for s in services if s.get("domain") == domain]
            return services
        # Unfiltered, the full payload is enormous; names alone are enough to
        # decide what to ask for next.
        return {"domains": sorted(s.get("domain") for s in services if s.get("domain"))}

    if action == "render_template":
        template = instruction.get("template")
        if not template:
            raise HomeAssistantError("render_template requires 'template'.")
        return rest.post("/api/template", json={"template": template})

    if action == "history":
        entity_id = instruction.get("entity_id")
        if not entity_id:
            raise HomeAssistantError("history requires 'entity_id'.")
        return rest.get(
            f"/api/history/period/{_since(instruction)}",
            params={"filter_entity_id": entity_id, "minimal_response": "true"},
        )

    if action == "logbook":
        return rest.get(f"/api/logbook/{_since(instruction)}")

    if action == "error_log":
        # Never returned raw: the endpoint serves the whole log file, which one
        # noisy integration can grow to hundreds of MB. See error_log.py.
        return error_log_mod.condense(
            rest.get("/api/error_log"),
            limit=instruction.get("limit"),
            max_chars=instruction.get("max_chars"),
        )

    if action == "check_config":
        return rest.post("/api/config/core/check_config")

    if action == "list_automations":
        return [
            {
                "entity_id": s.get("entity_id"),
                "name": s.get("attributes", {}).get("friendly_name"),
                "state": s.get("state"),
                "id": s.get("attributes", {}).get("id"),
            }
            for s in rest.get("/api/states")
            if str(s.get("entity_id", "")).startswith("automation.")
        ]

    if action == "get_automation":
        automation_id = instruction.get("id")
        if not automation_id:
            raise HomeAssistantError(
                "get_automation requires 'id' -- the numeric automation id from "
                "list_automations, not the entity_id."
            )
        return rest.get(f"/api/config/automation/config/{automation_id}")

    raise HomeAssistantError(f"Action '{action}' is routed but not implemented.")


def _since(instruction) -> str:
    """ISO timestamp `hours` ago, for the history and logbook endpoints."""
    from datetime import datetime, timedelta, timezone

    hours = int(instruction.get("hours", 24))
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
