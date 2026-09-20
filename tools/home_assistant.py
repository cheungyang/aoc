"""The single Home Assistant tool.

One tool, many actions, rather than one tool per capability. Two reasons:

  * Token cost. Exposing Home Assistant's MCP tools directly would inject ~20
    schemas into every prompt of every agent holding the grant, on every message.
    One schema is a fraction of that, and being static it sits in the cacheable
    prefix of the system prompt.
  * Stability. MCP tool names vary with the Home Assistant version and with which
    entities are exposed to Assist, so a static allow-list built from them drifts
    out of date on upgrade. The action vocabulary here is ours and does not move.

Which transport serves an action is decided by `routing.py`, never by the model.

Reads apply immediately. Mutating actions run the guard chain in `guards.py`
first, and config writes are snapshotted and verified by `writes.py` so a bad
change can be undone. `upsert_helper` is declared and guarded but not yet
dispatched; it is served by the WebSocket API.
"""
import json

from langchain_core.tools import tool

from core.integrations.homeassistant import guards
from core.integrations.homeassistant import inventory as inventory_mod
from core.integrations.homeassistant import writes as writes_mod
from core.integrations.homeassistant.client import HomeAssistantClient, HomeAssistantError
from core.integrations.homeassistant.routing import READ_ACTIONS, WRITE_ACTIONS
from core.integrations.homeassistant.ws import HomeAssistantWebSocket
from core.loaders.tools_loader import ToolsLoader
from core.runtime.execution_context import try_context
from core.util import format_tool_response

# Actions this phase can actually perform. Kept separate from routing.READ_ACTIONS
# so that `live_context` -- routed to MCP, which lands in Phase 6 -- produces a
# clear "not available yet" instead of an obscure failure.
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
# routing table and passes the guards, but is served by the WebSocket API with a
# different schema per helper domain, so it is not dispatched yet.
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


@tool
def home_assistant(instructions: list[dict]) -> str:
    """Read and control Home Assistant: entities, areas, devices, automations, state.

    Supports several actions in one call. Each instruction is a dict with an
    "action" key plus that action's arguments.

    Start with `inventory` to orient yourself, then `search_registry` to find
    specific entities. Avoid `list_entities` without filters: this home has
    ~1400 entities and the unfiltered list is not useful.

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
      {"action": "error_log"}
          Home Assistant's error log, useful after a change.
      {"action": "check_config"}
          Validates the running configuration.
      {"action": "list_automations"} / {"action": "get_automation", "id": "<id>"}
          Existing automations, and one automation's full config.

    Writing:
      {"action": "call_service", "entity_id": "light.porch", "service": "turn_on",
       "service_data": {"brightness": 180}}
          Calls a service. Reversible domains (light, switch, fan, media_player,
          climate in band, ...) apply immediately. Pass "domain" instead of
          "entity_id" for services that target no entity.
      {"action": "upsert_automation", "config": {...}, "id": "<optional>"}
          Creates an automation, or replaces it when "id" matches an existing one.
          Omit "id" to create; one is generated and returned.
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
    automatically. Some domains (locks, alarms, covers, valves, water heaters) are
    refused outright and cannot be granted.

    Args:
        instructions: List of action dicts, executed in order.

    Returns:
        An XML envelope with one <instruction_result> per instruction.
    """
    if not isinstance(instructions, list) or not instructions:
        return format_tool_response(
            "home_assistant",
            payload="",
            errors="'instructions' must be a non-empty list of action dicts.",
        )

    ctx = try_context()
    tools_loader = ToolsLoader()

    payload_elements = []
    error_elements = []

    # Registry data is expensive to fetch and several actions need it, so it is
    # loaded at most once per call and shared across the batch.
    registry_cache = {}
    rest_client = None

    for instruction in instructions:
        if not isinstance(instruction, dict):
            error_elements.append(
                f'<instruction_error>Each instruction must be a dict, got {type(instruction).__name__}.</instruction_error>'
            )
            continue

        action = instruction.get("action")

        # The permission target is the entity selector the action touches, which
        # keeps grants readable as {"light.*": [...]} in agent.json.
        target = instruction.get("entity_id") or instruction.get("domain") or "*"

        if action in WRITE_ACTIONS:
            if ctx is not None and not tools_loader.check_permission(ctx, "home_assistant", action, target):
                error_elements.append(
                    f'<instruction_error action="{action}">Agent {ctx.agent_id} lacks '
                    f'permission for \'{action}\' on {target}.</instruction_error>'
                )
                continue

            outcome = _guard_write(instruction, action, target, ctx, tools_loader)
            (payload_elements if outcome.startswith("<confirmation") else error_elements).append(outcome)
            continue

        if action not in IMPLEMENTED:
            known = ", ".join(sorted(IMPLEMENTED))
            hint = (
                " (routed to the MCP backend, which is not wired up yet)"
                if action in READ_ACTIONS else ""
            )
            error_elements.append(
                f'<instruction_error action="{action}">Unknown action{hint}. '
                f'Available: {known}</instruction_error>'
            )
            continue

        if ctx is not None and not tools_loader.check_permission(ctx, "home_assistant", action, target):
            error_elements.append(
                f'<instruction_error action="{action}">Agent {ctx.agent_id} lacks '
                f'permission for \'{action}\' on {target}.</instruction_error>'
            )
            continue

        try:
            if rest_client is None:
                rest_client = HomeAssistantClient()
            result = _dispatch(action, instruction, rest_client, registry_cache)
            payload_elements.append(
                f'<instruction_result action="{action}">{_render(result)}</instruction_result>'
            )
        except HomeAssistantError as exc:
            error_elements.append(f'<instruction_error action="{action}">{exc}</instruction_error>')
        except Exception as exc:  # noqa: BLE001 - surfaced to the agent, not swallowed
            error_elements.append(
                f'<instruction_error action="{action}">{type(exc).__name__}: {exc}</instruction_error>'
            )

    return format_tool_response(
        "home_assistant",
        payload="\n".join(payload_elements),
        errors="\n".join(error_elements) if error_elements else "None",
    )


def _guard_write(instruction, action, target, ctx, tools_loader) -> str:
    """Runs the guard chain for one mutating action, then dispatches it.

    Returns an XML element: a <confirmation_required> when a human is needed, an
    <instruction_error> when the action is refused or failed, or an
    <instruction_result> when it was applied and verified.

    Dispatch lives behind the guards rather than beside them, so there is no path
    to a mutating call that skips authorisation -- the only way to reach the write
    is to fall off the end of `guards.authorise` without it raising.
    """
    # Everything except the bookkeeping keys is what the human is approving, and
    # therefore what the confirmation token is bound to.
    payload = {k: v for k, v in instruction.items() if k not in ("action", "confirm_token")}

    blanket = False
    if ctx is not None:
        grant = tools_loader.get_tool_permissions(ctx, "home_assistant")
        blanket = grant is not None and (not grant or (isinstance(grant, list) and "*" in grant))

    try:
        guards.authorise(
            action=action,
            target=target if target != "*" else None,
            payload=payload,
            confirm_token=instruction.get("confirm_token"),
            agent_id=getattr(ctx, "agent_id", None),
            blanket_grant=blanket,
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
        )
    except guards.GuardRejection as rejection:
        return f'<instruction_error action="{action}">{rejection}</instruction_error>'

    if action not in WRITE_IMPLEMENTED:
        return (
            f'<instruction_error action="{action}">Guards passed, but \'{action}\' is not '
            f'dispatched yet (it is served by the WebSocket API and lands with helper '
            f'support).</instruction_error>'
        )

    try:
        result = _dispatch_write(action, instruction, payload, ctx)
    except HomeAssistantError as exc:
        return f'<instruction_error action="{action}">{exc}</instruction_error>'
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent, not swallowed
        return f'<instruction_error action="{action}">{type(exc).__name__}: {exc}</instruction_error>'

    # A WriteResult that was applied but not verified has been rolled back (or
    # failed to roll back). Either way it is not a success, so it is reported as
    # an error -- but with the full result attached, because the snapshot path and
    # the rollback outcome are exactly what the agent needs to relay.
    if isinstance(result, writes_mod.WriteResult) and not result.verified:
        return (
            f'<instruction_error action="{action}">{_render(result.as_dict())}</instruction_error>'
        )

    rendered = result.as_dict() if isinstance(result, writes_mod.WriteResult) else result
    return f'<instruction_result action="{action}">{_render(rendered)}</instruction_result>'


def _dispatch_write(action, instruction, payload, ctx):
    """Performs one authorised mutating action."""
    rest = HomeAssistantClient()
    agent_id = getattr(ctx, "agent_id", None)

    if action == "call_service":
        domain = instruction.get("domain")
        service = instruction.get("service")
        entity_id = instruction.get("entity_id")

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
        return rest.get("/api/error_log")

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
