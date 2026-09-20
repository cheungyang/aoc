"""Tests for the agent-facing home_assistant tool.

Covers the parts the agent depends on: the batch envelope, per-action dispatch,
that read-only really is read-only, and that permission denials are refusals
rather than silent no-ops. The REST client and WebSocket are both faked.
"""
import unittest
from unittest.mock import patch

from core.integrations.homeassistant.client import HomeAssistantError
from tools import home_assistant as ha_tool


STATES = [
    {"entity_id": "light.porch", "state": "on", "attributes": {"friendly_name": "Porch"}},
    {"entity_id": "light.hall", "state": "off", "attributes": {}},
    {"entity_id": "switch.pump", "state": "off", "attributes": {}},
    {"entity_id": "automation.dusk", "state": "on",
     "attributes": {"friendly_name": "Dusk", "id": "1740941848966"}},
]


class FakeRest:
    def __init__(self, overrides=None):
        self.overrides = overrides or {}
        self.calls = []

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


def run(instructions, rest=None, ws=None):
    rest = rest or FakeRest()
    ws = ws or FakeWs()
    with patch.object(ha_tool, "HomeAssistantClient", lambda *a, **k: rest), \
         patch.object(ha_tool, "HomeAssistantWebSocket", lambda *a, **k: ws), \
         patch.object(ha_tool, "try_context", lambda: None):
        return ha_tool.home_assistant.invoke({"instructions": instructions})


class TestEnvelope(unittest.TestCase):
    def test_empty_instructions_are_rejected(self):
        out = ha_tool.home_assistant.invoke({"instructions": []})
        self.assertIn("non-empty list", out)

    def test_non_dict_instruction_is_rejected_by_the_schema(self):
        """The `list[dict]` annotation is the real enforcement: pydantic rejects
        this before the function body runs, so the agent gets a validation error
        rather than a partial result."""
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            ha_tool.home_assistant.invoke({"instructions": ["not a dict"]})

    def test_non_dict_guard_degrades_gracefully_when_called_directly(self):
        """Belt-and-braces: the in-body check still holds for non-schema callers."""
        out = ha_tool.home_assistant.func(["not a dict"])
        self.assertIn("must be a dict", out)

    def test_batch_runs_every_instruction(self):
        out = run([{"action": "get_config"}, {"action": "error_log"}])
        self.assertIn('action="get_config"', out)
        self.assertIn('action="error_log"', out)
        self.assertIn("<errors>None</errors>", out)

    def test_one_failure_does_not_abort_the_batch(self):
        out = run([{"action": "get_state"}, {"action": "error_log"}])
        self.assertIn("requires 'entity_id'", out)   # first failed
        self.assertIn("no errors", out)              # second still ran


class TestWritesDisabled(unittest.TestCase):
    """With HA_WRITE_ENABLED off, no mutating action may get through."""

    def test_write_actions_are_refused(self):
        """Every mutating action must be turned away while the switch is off.

        Asserted on the shape of the refusal -- an <instruction_error> for that
        action, and nothing in the payload -- rather than on the wording. The
        message has already changed once (the HA_WRITE_ENABLED kill switch
        replaced an earlier "arrives later" note), and matching prose makes this
        fail for a rewording rather than for a real regression.
        """
        for action in ["call_service", "upsert_automation", "delete_automation", "reload"]:
            out = run([{"action": action}])
            self.assertIn(f'<instruction_error action="{action}"', out, action)
            self.assertIn("<payload></payload>", out, action)

    def test_write_actions_never_reach_the_client(self):
        rest = FakeRest()
        run([{"action": "call_service", "domain": "light"}], rest=rest)
        self.assertEqual(rest.calls, [])

    def test_unknown_action_lists_the_available_ones(self):
        out = run([{"action": "nope"}])
        self.assertIn("Unknown action", out)
        self.assertIn("inventory", out)

    def test_unimplemented_backend_says_so(self):
        out = run([{"action": "live_context"}])
        self.assertIn("MCP backend", out)


class TestWriteEnvelope(unittest.TestCase):
    """Where a write outcome lands in the response envelope.

    Regression: outcomes from the write path were sorted by asking "is this a
    confirmation?", with everything else treated as an error. That was correct
    while a write could only be refused or deferred, but once writes could
    succeed it filed every applied change under <errors> -- telling the agent its
    change had failed when it had in fact been applied.

    The live probe missed this because it asserted the result string appeared
    somewhere in the response, not that it appeared in the right section. These
    assert on the section.
    """

    def setUp(self):
        from core.integrations.homeassistant import guards

        patcher = patch.object(guards, "write_enabled", lambda: True)
        patcher.start()
        self.addCleanup(patcher.stop)
        guards.reset_pending()
        self.addCleanup(guards.reset_pending)

    @staticmethod
    def _payload(out):
        return out.split("<payload>")[1].split("</payload>")[0]

    @staticmethod
    def _errors(out):
        return out.split("<errors>")[1].split("</errors>")[0]

    def test_a_successful_write_is_reported_as_a_success(self):
        out = run([{"action": "call_service", "entity_id": "light.porch", "service": "turn_on"}])

        self.assertIn('<instruction_result action="call_service"', self._payload(out))
        self.assertEqual(self._errors(out).strip(), "None")

    def test_a_confirmation_request_is_not_an_error(self):
        out = run([{"action": "upsert_automation", "id": "a1", "config": {"alias": "X"}}])

        self.assertIn("<confirmation_required", self._payload(out))
        self.assertEqual(self._errors(out).strip(), "None")

    def test_a_refused_write_is_an_error(self):
        out = run([{"action": "call_service", "entity_id": "lock.front", "service": "unlock"}])

        self.assertIn('<instruction_error action="call_service"', self._errors(out))
        self.assertEqual(self._payload(out).strip(), "")

    def test_a_successful_write_does_not_poison_a_batch(self):
        """A write succeeding must not make its read siblings look failed."""
        out = run([
            {"action": "call_service", "entity_id": "light.porch", "service": "turn_on"},
            {"action": "get_config"},
        ])

        payload = self._payload(out)
        self.assertIn('<instruction_result action="call_service"', payload)
        self.assertIn('<instruction_result action="get_config"', payload)
        self.assertEqual(self._errors(out).strip(), "None")


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
        class Ctx:
            agent_id = "test-agent"

        rest = FakeRest()
        with patch.object(ha_tool, "HomeAssistantClient", lambda *a, **k: rest), \
             patch.object(ha_tool, "try_context", lambda: Ctx()), \
             patch.object(ha_tool.ToolsLoader, "check_permission", lambda *a, **k: False):
            out = ha_tool.home_assistant.invoke({"instructions": [{"action": "get_config"}]})

        self.assertIn("lacks permission", out)
        self.assertEqual(rest.calls, [])

    def test_permission_target_is_the_entity_selector(self):
        seen = {}

        class Ctx:
            agent_id = "test-agent"

        def capture(self, ctx, tool_id, action=None, path=None, **kw):
            seen["path"] = path
            return True

        with patch.object(ha_tool, "HomeAssistantClient", lambda *a, **k: FakeRest()), \
             patch.object(ha_tool, "try_context", lambda: Ctx()), \
             patch.object(ha_tool.ToolsLoader, "check_permission", capture):
            ha_tool.home_assistant.invoke(
                {"instructions": [{"action": "get_state", "entity_id": "light.porch"}]}
            )

        self.assertEqual(seen["path"], "light.porch")


if __name__ == "__main__":
    unittest.main()
