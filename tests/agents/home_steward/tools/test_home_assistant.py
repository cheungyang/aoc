"""Tests for the agent-facing home_assistant tool.

Covers the parts the agent depends on: the batch envelope, per-action dispatch,
that read-only really is read-only, and that permission denials are refusals
rather than silent no-ops. Both backends -- REST and WebSocket -- are faked, so
no test reaches the live Home Assistant instance.

Calls run in home-steward's ExecutionContext with ToolsLoader's answers patched:
every check allowed, under an explicit grant, unless a test says otherwise.
"""
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from core.loaders.tools_loader import ToolsLoader
from agents.home_steward.lib.homeassistant.client import HomeAssistantError
from agents.home_steward.tools import home_assistant as ha_tool
from tests.helpers import envelope, execution_context


STATES = [
    {"entity_id": "light.porch", "state": "on", "attributes": {"friendly_name": "Porch"}},
    {"entity_id": "light.hall", "state": "off", "attributes": {}},
    {"entity_id": "switch.pump", "state": "off", "attributes": {}},
    {"entity_id": "automation.dusk", "state": "on",
     "attributes": {"friendly_name": "Dusk", "id": "1740941848966"}},
]

AUTOMATION = {
    "alias": "Porch Light Sunset",
    "trigger": [{"platform": "sun", "event": "sunset"}],
    "action": [{"service": "light.turn_on", "target": {"entity_id": "light.porch"}}],
}

# One well-formed instruction per kind of write, for tests about the layers
# after the shape check (kill switch, envelope). A malformed one would be refused
# by `_validate_write` first and prove nothing about those layers.
WELL_FORMED_WRITES = [
    {"action": "call_service", "entity_id": "light.porch", "service": "turn_on"},
    {"action": "upsert_automation", "config": AUTOMATION},
    {"action": "delete_automation", "id": "a1"},
    {"action": "reload", "domain": "automation"},
]


class FakeRest:
    def __init__(self, overrides=None):
        self.overrides = overrides or {}
        self.calls = []
        self.bodies = []

    def get(self, path, params=None):
        self.calls.append(("GET", path))
        if path in self.overrides:
            return self.overrides[path]
        if path == "/api/states":
            return STATES
        if path == "/api/config":
            return {"version": "2026.6.4", "location_name": "Home",
                    "components": ["a"] * 500, "allowlist_external_dirs": ["/config/www"]}
        if path == "/api/services":
            return [{"domain": "light", "services": {"turn_on": {}}},
                    {"domain": "switch", "services": {"turn_on": {}}}]
        if path == "/api/error_log":
            return "no errors"
        if path.startswith("/api/states/"):
            entity_id = path.rsplit("/", 1)[1]
            match = [s for s in STATES if s["entity_id"] == entity_id]
            if not match:
                raise HomeAssistantError(f"Home Assistant returned 404 for GET {path}")
            return match[0]
        return {}

    def post(self, path, json=None, retry=True):
        self.calls.append(("POST", path))
        self.bodies.append((path, json))
        if path == "/api/template":
            return "2"
        if path.startswith("/api/services/"):
            # HA answers a service call with the list of states it changed.
            return [{"entity_id": "light.porch"}]
        return {"result": "valid"}

    def delete(self, path, retry=True):
        self.calls.append(("DELETE", path))
        return {"result": "ok"}


class FakeWs:
    def __init__(self):
        self.entered = 0

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, *a):
        return False

    def entities(self):
        return [
            {"entity_id": "light.porch", "device_id": "d1", "original_name": "Porch"},
            {"entity_id": "light.hall", "device_id": "d1"},
            {"entity_id": "switch.pump", "device_id": "d2"},
            {"entity_id": "sensor.off", "device_id": "d2", "disabled_by": "user"},
        ]

    def devices(self):
        return [{"id": "d1", "area_id": "porch"}, {"id": "d2", "area_id": None}]

    def areas(self):
        return [{"area_id": "porch", "name": "Porch"}]


AGENT = "home-steward"
# An explicit grant, as in agent.json. A blanket one ({}) would refuse every write.
GRANT = {"*": ["@observe"], "light.*": ["@control"]}


def allow_all(self, ctx, tool_id, action=None, path=None, **kwargs):
    return True


@contextmanager
def granted(check=allow_all, grant=None):
    """home-steward's context; ToolsLoader answers `check` and returns `grant` (GRANT by default)."""
    shape = GRANT if grant is None else grant
    with execution_context(agent_id=AGENT), \
         patch.object(ToolsLoader, "check_permission", check), \
         patch.object(ToolsLoader, "get_tool_permissions", lambda *a, **k: shape):
        yield


def run(instructions, rest=None, ws=None, check=allow_all, grant=None):
    """The tool's raw reply; `envelope(run(...))` reads it."""
    rest = rest or FakeRest()
    ws = ws or FakeWs()
    with granted(check, grant), \
         patch.object(ha_tool, "HomeAssistantClient", lambda *a, **k: rest), \
         patch.object(ha_tool, "HomeAssistantWebSocket", lambda *a, **k: ws):
        return ha_tool.home_assistant.invoke({"instructions": instructions})


class TestEnvelope(unittest.TestCase):
    def test_empty_instructions_are_rejected(self):
        with granted():
            out = envelope(ha_tool.home_assistant.invoke({"instructions": []}))
        self.assertEqual(out["status"], "failed")
        self.assertIn("non-empty list", out["errors"])

    def test_non_dict_instruction_is_rejected_by_the_schema(self):
        """The `list[dict]` annotation is the real enforcement: pydantic rejects
        this before the function body runs, so the agent gets a validation error
        rather than a partial result."""
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            ha_tool.home_assistant.invoke({"instructions": ["not a dict"]})

    def test_non_dict_guard_degrades_gracefully_when_called_directly(self):
        """Belt-and-braces: the in-body check still holds for non-schema callers."""
        with granted():
            out = envelope(ha_tool.home_assistant.func(instructions=["not a dict"]))
        self.assertIn("- instruction 1: Each instruction must be a dict, got str.", out["issues"])

    def test_needs_an_agent_run(self):
        out = envelope(ha_tool.home_assistant.invoke({"instructions": [{"action": "get_config"}]}))
        self.assertIn("no active execution context", out["errors"])

    def test_batch_runs_every_instruction(self):
        out = envelope(run([{"action": "get_config"}, {"action": "error_log"}]))
        self.assertEqual(out["status"], "ok")
        self.assertIn('action="get_config"', out["payload"])
        self.assertIn('action="error_log"', out["payload"])
        self.assertEqual(out["issues"], "None")
        self.assertEqual(out["errors"], "None")

    def test_one_failure_does_not_abort_the_batch(self):
        out = envelope(run([{"action": "get_state"}, {"action": "error_log"}]))
        self.assertEqual(out["status"], "partial")
        self.assertIn("- get_state: get_state requires 'entity_id'.", out["issues"])   # first failed
        self.assertIn("no errors", out["payload"])                                      # second still ran

    def test_nothing_done_is_failed(self):
        out = envelope(run([{"action": "get_state"}]))
        self.assertEqual(out["status"], "failed")
        self.assertIn("requires 'entity_id'", out["issues"])
        self.assertEqual(out["errors"], "Error: Nothing was done; the issues say why.")

    def test_error_log_is_condensed(self):
        """A noisy log must not be returned raw: one was measured at 150 MB."""
        entry = ("2026-09-21 21:21:08.584 ERROR (MainThread) [x] poll failed\n"
                 "Traceback (most recent call last):\n  boom\nConnectionError: refused\n")
        rest = FakeRest({"/api/error_log": entry * 20000})
        out = run([{"action": "error_log"}], rest=rest)
        self.assertLess(len(out), 25000)
        self.assertIn("[x20000,", out)


class TestWritesDisabled(unittest.TestCase):
    """With HA_WRITE_ENABLED off, no mutating action may get through."""

    def test_write_actions_are_refused(self):
        """Every mutating action must be turned away while the switch is off.

        Asserted on the shape of the refusal -- an issue naming that action, and
        nothing in the payload -- rather than on the wording. The message has
        already changed once (the HA_WRITE_ENABLED kill switch replaced an earlier
        "arrives later" note), and matching prose makes this fail for a rewording
        rather than for a real regression.
        """
        for instruction in WELL_FORMED_WRITES:
            action = instruction["action"]
            # Well formed, so the refusal below comes from the switch, not the shape check.
            self.assertIsNone(ha_tool._validate_write(action, instruction), action)
            out = envelope(run([instruction]))
            self.assertTrue(out["issues"].startswith(f"- {action}"), action)
            self.assertEqual(out["payload"], "", action)
            self.assertEqual(out["status"], "failed", action)

    def test_write_actions_never_reach_the_client(self):
        rest = FakeRest()
        run(WELL_FORMED_WRITES, rest=rest)
        self.assertEqual(rest.calls, [])

    def test_unknown_action_lists_the_available_ones(self):
        out = envelope(run([{"action": "nope"}]))
        self.assertIn("- nope: Unknown action", out["issues"])
        self.assertIn("inventory", out["issues"])

    def test_a_failed_read_does_not_abort_the_batch(self):
        """One failed read must not cost the agent the rest of its answers."""
        class ConfigDown(FakeRest):
            def get(self, path, params=None):
                if path == "/api/config":
                    raise HomeAssistantError("Could not reach Home Assistant at http://x")
                return super().get(path, params)

        raw = run([{"action": "get_config"}, {"action": "get_state", "entity_id": "light.porch"}],
                  rest=ConfigDown())
        out = envelope(raw)

        self.assertIn("- get_config: Could not reach Home Assistant at http://x", out["issues"])
        self.assertNotIn("Traceback", raw)
        self.assertIn("light.porch", out["payload"])
        self.assertEqual(out["status"], "partial")


class TestWriteEnvelope(unittest.TestCase):
    """Where a write outcome lands in the response envelope.

    Regression: outcomes from the write path were sorted by asking "is this a
    confirmation?", with everything else treated as an error. That was correct
    while a write could only be refused or deferred, but once writes could
    succeed it filed every applied change under <errors> -- telling the agent its
    change had failed when it had in fact been applied.

    The live probe missed this because it asserted the result string appeared
    somewhere in the response, not that it appeared in the right section. These
    assert on the section: results and confirmations in the payload, refusals
    under issues.
    """

    def setUp(self):
        from agents.home_steward.lib.homeassistant import guards

        patcher = patch.object(guards, "write_enabled", lambda: True)
        patcher.start()
        self.addCleanup(patcher.stop)
        guards.reset_pending()
        self.addCleanup(guards.reset_pending)

    def test_a_successful_write_is_reported_as_a_success(self):
        out = envelope(run([{"action": "call_service", "entity_id": "light.porch", "service": "turn_on"}]))

        self.assertIn('<instruction_result action="call_service"', out["payload"])
        self.assertEqual(out["issues"], "None")
        self.assertEqual(out["status"], "ok")

    def test_a_confirmation_request_is_not_an_error(self):
        out = envelope(run([{"action": "upsert_automation", "id": "a1", "config": AUTOMATION}]))

        self.assertIn("<confirmation_required", out["payload"])
        self.assertEqual(out["issues"], "None")
        self.assertEqual(out["status"], "ok")

    def test_a_refused_write_is_an_issue(self):
        out = envelope(run([{"action": "call_service", "entity_id": "lock.front", "service": "unlock"}]))

        self.assertIn("- call_service lock.front: 'lock' is on the permanent deny-list", out["issues"])
        self.assertEqual(out["payload"], "")
        self.assertEqual(out["status"], "failed")

    def test_a_successful_write_does_not_poison_a_batch(self):
        """A write succeeding must not make its read siblings look failed."""
        out = envelope(run([
            {"action": "call_service", "entity_id": "light.porch", "service": "turn_on"},
            {"action": "get_config"},
        ]))

        self.assertIn('<instruction_result action="call_service"', out["payload"])
        self.assertIn('<instruction_result action="get_config"', out["payload"])
        self.assertEqual(out["issues"], "None")

    def test_a_refused_write_does_not_fail_the_batch(self):
        """The refusal stays with its instruction; the light still turns on."""
        out = envelope(run([
            {"action": "call_service", "entity_id": "light.porch", "service": "turn_on"},
            {"action": "call_service", "entity_id": "lock.front", "service": "unlock"},
        ]))

        self.assertEqual(out["status"], "partial")
        self.assertIn('<instruction_result action="call_service"', out["payload"])
        self.assertIn("- call_service lock.front: 'lock' is on the permanent deny-list", out["issues"])
        self.assertEqual(out["errors"], "None")


class _WritesEnabled(unittest.TestCase):
    def setUp(self):
        from agents.home_steward.lib.homeassistant import guards

        patcher = patch.object(guards, "write_enabled", lambda: True)
        patcher.start()
        self.addCleanup(patcher.stop)
        guards.reset_pending()
        self.addCleanup(guards.reset_pending)
        self.guards = guards

    def assert_refused_before_a_token(self, instruction, *fragments):
        """Refused as an issue, with no token minted and nothing sent to HA."""
        rest = FakeRest()
        out = run([instruction], rest=rest)
        self.assertTrue(envelope(out)["issues"].startswith(f"- {instruction['action']}"), out)
        self.assertNotIn("<confirm_token>", out)
        self.assertEqual(self.guards._pending, {}, "a confirmation token was issued")
        self.assertEqual(rest.calls, [])
        for fragment in fragments:
            self.assertIn(fragment, out)
        return out


class TestWriteShape(_WritesEnabled):
    """A malformed write fails before any confirmation is issued.

    Regression: home-steward sent an automation under "automation" rather than
    "config". The guards minted a token, the user approved the diff, and only the
    apply step noticed "config" was missing -- an approval spent on a change that
    could never land. The shape check now runs before the guards.
    """

    def test_every_dispatched_write_has_a_key_list(self):
        self.assertEqual(set(ha_tool._WRITE_KEYS), set(ha_tool.WRITE_IMPLEMENTED))

    def test_automation_under_the_wrong_key_is_refused_before_a_token(self):
        self.assert_refused_before_a_token(
            {"action": "upsert_automation", "automation": AUTOMATION}, "'automation'", "'config'"
        )

    def test_config_without_triggers_or_actions_is_refused(self):
        self.assert_refused_before_a_token(
            {"action": "upsert_automation", "config": {"alias": "X"}}, "'triggers'", "'actions'"
        )

    def test_old_and_new_spellings_of_triggers_and_actions_are_accepted(self):
        renamed = {"alias": "X", "triggers": [{"trigger": "sun", "event": "sunset"}], "actions": []}
        for config in (AUTOMATION, renamed):
            out = run([{"action": "upsert_automation", "config": config}])
            self.assertIn("<confirmation_required", out)

    def test_script_without_an_id_is_refused_before_a_token(self):
        self.assert_refused_before_a_token(
            {"action": "upsert_script", "config": {"sequence": []}}, "requires 'id'"
        )

    def test_delete_without_an_id_is_refused_before_a_token(self):
        self.assert_refused_before_a_token({"action": "delete_automation"}, "requires 'id'")

    def test_an_undispatched_write_is_refused_before_a_token(self):
        self.assert_refused_before_a_token(
            {"action": "upsert_helper", "config": {"name": "Guest mode"}}, "not dispatched"
        )


class TestServiceTarget(_WritesEnabled):
    """call_service honours Home Assistant's `target.entity_id` shape.

    Regression: the tool read only a top-level `entity_id`, so a call written the
    way automations write it -- `target: {"entity_id": ...}` -- reached Home
    Assistant with no entity at all, while the guards had judged the entity.
    """

    def test_target_entity_id_is_sent_to_home_assistant(self):
        for entity in ("light.porch", ["light.porch"]):
            rest = FakeRest()
            out = run([{"action": "call_service", "service": "turn_on",
                        "target": {"entity_id": entity}}], rest=rest)
            self.assertIn('<instruction_result action="call_service"', out)
            self.assertEqual(rest.bodies, [("/api/services/light/turn_on", {"entity_id": "light.porch"})])

    def test_a_blocked_domain_in_target_is_still_refused(self):
        rest = FakeRest()
        out = run([{"action": "call_service", "service": "unlock",
                    "target": {"entity_id": "lock.front"}}], rest=rest)
        self.assertIn("- call_service lock.front: 'lock' is on the permanent deny-list", envelope(out)["issues"])
        self.assertEqual(rest.calls, [])

    def test_several_entities_in_one_instruction_are_refused(self):
        for instruction in (
            {"action": "call_service", "service": "turn_on",
             "target": {"entity_id": ["light.porch", "light.hall"]}},
            {"action": "call_service", "service": "turn_on", "entity_id": ["light.porch", "light.hall"]},
        ):
            self.assert_refused_before_a_token(instruction, "one entity per instruction")

    def test_area_targets_are_refused(self):
        self.assert_refused_before_a_token(
            {"action": "call_service", "domain": "light", "service": "turn_off",
             "target": {"area_id": "kitchen"}},
            "search_registry",
        )

    def test_the_entity_is_named_once(self):
        self.assert_refused_before_a_token(
            {"action": "call_service", "service": "turn_on", "entity_id": "light.porch",
             "target": {"entity_id": "light.hall"}},
            "not both",
        )

    def test_domain_must_match_the_entity(self):
        """An allowed light must not carry a script past the guards unconfirmed."""
        self.assert_refused_before_a_token(
            {"action": "call_service", "domain": "script", "service": "goodnight",
             "entity_id": "light.porch"},
            "does not match",
        )

    def test_permissions_judge_the_target_entity(self):
        seen = []

        def capture(self, ctx, tool_id, action=None, path=None, **kw):
            if action is not None:  # the per-instruction check, not the tool-level one
                seen.append((action, path))
            return True

        run([{"action": "call_service", "service": "turn_on", "target": {"entity_id": "light.porch"}}],
            check=capture, grant={"light.*": ["call_service"]})

        self.assertEqual(seen, [("call_service", "light.porch")])


class TestActions(unittest.TestCase):
    def test_inventory_summarises(self):
        out = run([{"action": "inventory"}])
        self.assertIn('"active": 3', out)
        self.assertIn('"disabled": 1', out)
        self.assertIn("Porch", out)

    def test_search_registry_filters(self):
        out = run([{"action": "search_registry", "domain": "light", "area": "Porch"}])
        self.assertIn("light.porch", out)
        self.assertNotIn("switch.pump", out)

    def test_get_state_requires_and_uses_entity_id(self):
        out = run([{"action": "get_state", "entity_id": "light.porch"}])
        self.assertIn('"state": "on"', out)

    def test_get_state_surfaces_a_404_as_an_error(self):
        out = run([{"action": "get_state", "entity_id": "light.ghost"}])
        self.assertIn("404", out)

    def test_list_entities_filters_by_domain_and_caps(self):
        out = run([{"action": "list_entities", "domain": "light"}])
        self.assertIn("light.porch", out)
        self.assertNotIn("switch.pump", out)

    def test_get_config_is_summarised_by_default(self):
        out = run([{"action": "get_config"}])
        self.assertIn('"components_count": 500', out)
        self.assertNotIn("allowlist_external_dirs", out)

    def test_get_config_full_returns_everything(self):
        out = run([{"action": "get_config", "full": True}])
        self.assertIn("allowlist_external_dirs", out)

    def test_list_services_unfiltered_returns_domain_names_only(self):
        out = run([{"action": "list_services"}])
        self.assertIn("domains", out)
        self.assertNotIn("turn_on", out)

    def test_render_template_posts_the_template(self):
        rest = FakeRest()
        out = run([{"action": "render_template", "template": "{{ 1 + 1 }}"}], rest=rest)
        self.assertIn(("POST", "/api/template"), rest.calls)
        self.assertIn("2", out)

    def test_render_template_requires_a_template(self):
        self.assertIn("requires 'template'", run([{"action": "render_template"}]))

    def test_list_automations_extracts_the_numeric_id(self):
        out = run([{"action": "list_automations"}])
        self.assertIn("1740941848966", out)
        self.assertIn("automation.dusk", out)

    def test_get_automation_explains_which_id_it_wants(self):
        out = run([{"action": "get_automation"}])
        self.assertIn("not the entity_id", out)

    def test_registries_are_fetched_once_per_batch(self):
        """Two registry-backed actions must not mean two WebSocket sessions."""
        ws = FakeWs()
        run([{"action": "inventory"}, {"action": "search_registry", "domain": "light"}], ws=ws)
        self.assertEqual(ws.entered, 1)


class TestPermissions(unittest.TestCase):
    def test_denied_action_is_refused_and_never_dispatched(self):
        def tool_only(self, ctx, tool_id, action=None, path=None, **kw):
            return action is None  # the tool is granted; get_config on "*" is not

        rest = FakeRest()
        out = envelope(run([{"action": "get_config"}], rest=rest, check=tool_only))

        self.assertIn("- get_config: Agent home-steward lacks permission for 'get_config' on *.", out["issues"])
        self.assertEqual(out["status"], "failed")
        self.assertEqual(rest.calls, [])

    def test_permission_target_is_the_entity_selector(self):
        seen = []

        def capture(self, ctx, tool_id, action=None, path=None, **kw):
            if action is not None:
                seen.append((action, path))
            return True

        run([{"action": "get_state", "entity_id": "light.porch"}], check=capture)

        self.assertEqual(seen, [("get_state", "light.porch")])

    def test_a_blanket_grant_refuses_writes_but_not_reads(self):
        from agents.home_steward.lib.homeassistant import guards

        with patch.object(guards, "write_enabled", lambda: True):
            out = envelope(run([
                {"action": "call_service", "entity_id": "light.porch", "service": "turn_on"},
                {"action": "get_config"},
            ], grant={}))

        self.assertIn("- call_service light.porch: home_assistant requires explicit per-action grants for writes",
                      out["issues"])
        self.assertIn('<instruction_result action="get_config"', out["payload"])
        self.assertNotIn('action="call_service"', out["payload"])
        self.assertEqual(out["status"], "partial")


if __name__ == "__main__":
    unittest.main()
