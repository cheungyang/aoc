"""Tests for the write guards.

These are the tests that matter most in this integration: they are what stands
between an agent and the physical locks on the house. Each bypass the design
anticipated has a test named after it, so a future refactor that reopens one
fails here rather than in the hallway.
"""
import time
import unittest
from unittest.mock import patch

from core.integrations.homeassistant import guards


def enable_writes():
    return patch.object(guards, "write_enabled", lambda: True)


class TestKillSwitch(unittest.TestCase):
    def test_writes_are_refused_when_disabled(self):
        with patch.object(guards, "write_enabled", lambda: False):
            with self.assertRaises(guards.GuardRejection) as ctx:
                guards.authorise("call_service", target="light.porch")
        self.assertIn("HA_WRITE_ENABLED", str(ctx.exception))

    def test_kill_switch_precedes_everything_else(self):
        """Disabled should refuse even an otherwise-auto-applying action."""
        with patch.object(guards, "write_enabled", lambda: False):
            with self.assertRaises(guards.GuardRejection):
                guards.authorise("call_service", target="light.porch")


class TestDenyList(unittest.TestCase):
    def setUp(self):
        guards.reset_pending()

    def test_each_blocked_domain_is_refused(self):
        with enable_writes():
            for domain in guards.BLOCKED_DOMAINS:
                with self.assertRaises(guards.GuardRejection, msg=domain) as ctx:
                    guards.authorise("call_service", target=f"{domain}.whatever")
                self.assertIn("deny-list", str(ctx.exception))

    def test_vacuum_is_not_blocked(self):
        """Removed from the list during review; this pins that decision."""
        self.assertNotIn("vacuum", guards.BLOCKED_DOMAINS)

    def test_blocked_domain_is_not_merely_confirmable(self):
        """A refusal must not degrade into 'ask the human' -- there is no token
        that makes unlocking the door acceptable."""
        with enable_writes():
            with self.assertRaises(guards.GuardRejection):
                guards.authorise("call_service", target="lock.front_door")

    def test_bare_domain_is_matched_as_well_as_entity_id(self):
        with enable_writes():
            with self.assertRaises(guards.GuardRejection):
                guards.authorise("call_service", target="lock")

    def test_allowed_domain_passes(self):
        with enable_writes():
            guards.authorise("call_service", target="light.porch")  # no raise


class TestPayloadScanning(unittest.TestCase):
    """The bypass: reaching a blocked domain by writing config instead of calling it."""

    def setUp(self):
        guards.reset_pending()

    def test_automation_that_actuates_a_blocked_domain_is_refused(self):
        payload = {
            "id": "1",
            "config": {
                "trigger": [{"platform": "sun", "event": "sunset"}],
                "action": [{"service": "lock.unlock", "target": {"entity_id": "lock.front_door"}}],
            },
        }
        with enable_writes():
            with self.assertRaises(guards.GuardRejection) as ctx:
                guards.authorise("upsert_automation", payload=payload)
        self.assertIn("lock", str(ctx.exception))

    def test_blocked_reference_is_found_at_any_depth(self):
        payload = {"config": {"a": [{"b": {"c": ["cover.garage"]}}]}}
        with enable_writes():
            with self.assertRaises(guards.GuardRejection) as ctx:
                guards.authorise("upsert_script", payload=payload)
        self.assertIn("cover.garage", str(ctx.exception))

    def test_error_names_the_offending_path(self):
        payload = {"config": {"action": [{"service": "valve.open"}]}}
        with enable_writes():
            with self.assertRaises(guards.GuardRejection) as ctx:
                guards.authorise("upsert_automation", payload=payload)
        self.assertIn("config.action[0].service", str(ctx.exception))

    def test_harmless_payload_is_not_flagged(self):
        payload = {"config": {"action": [{"service": "light.turn_on",
                                          "target": {"entity_id": "light.porch"}}]}}
        with enable_writes():
            with self.assertRaises(guards.ConfirmationRequired):
                guards.authorise("upsert_automation", payload=payload)

    def test_prose_mentioning_a_domain_is_not_flagged(self):
        """'the lock is fine' must not trip the scanner; only entity/service refs do."""
        payload = {"config": {"alias": "Remind me the lock is fine", "description": "lock stuff"}}
        with enable_writes():
            with self.assertRaises(guards.ConfirmationRequired):
                guards.authorise("upsert_automation", payload=payload)


class TestClimateBand(unittest.TestCase):
    def setUp(self):
        guards.reset_pending()

    def test_setpoint_above_the_band_is_refused(self):
        with enable_writes():
            with self.assertRaises(guards.GuardRejection) as ctx:
                guards.authorise("call_service", target="climate.hall",
                                 payload={"service_data": {"temperature": 81}})
        self.assertIn("safe band", str(ctx.exception))

    def test_setpoint_below_the_band_is_refused(self):
        with enable_writes():
            with self.assertRaises(guards.GuardRejection):
                guards.authorise("call_service", target="climate.hall",
                                 payload={"service_data": {"temperature": 4}})

    def test_setpoint_inside_the_band_is_allowed(self):
        with enable_writes():
            guards.authorise("call_service", target="climate.hall",
                             payload={"service_data": {"temperature": 20}})

    def test_non_numeric_setpoint_is_refused(self):
        with enable_writes():
            with self.assertRaises(guards.GuardRejection):
                guards.authorise("call_service", target="climate.hall",
                                 payload={"service_data": {"temperature": "warm"}})


class TestAutonomyPolicy(unittest.TestCase):
    def test_reversible_domains_auto_apply(self):
        for domain in ["light", "switch", "fan", "media_player", "scene"]:
            self.assertFalse(guards.confirmation_required("call_service", f"{domain}.x"), domain)

    def test_scripts_and_automations_require_confirmation(self):
        """The opaque-effects bypass: a script can contain lock.unlock."""
        self.assertTrue(guards.confirmation_required("call_service", "script.anything"))
        self.assertTrue(guards.confirmation_required("call_service", "automation.anything"))

    def test_config_writes_always_require_confirmation(self):
        for action in guards.CONFIG_WRITE_ACTIONS:
            self.assertTrue(guards.confirmation_required(action), action)

    def test_unknown_domains_default_to_requiring_confirmation(self):
        """A domain nobody has classified should arrive as 'ask', not 'assume safe'."""
        self.assertTrue(guards.confirmation_required("call_service", "brand_new_domain.x"))


class TestConfirmationTokens(unittest.TestCase):
    def setUp(self):
        guards.reset_pending()

    def test_first_attempt_returns_a_token(self):
        with enable_writes():
            with self.assertRaises(guards.ConfirmationRequired) as ctx:
                guards.authorise("upsert_automation", payload={"id": "1"})
        self.assertTrue(ctx.exception.token)

    def test_token_allows_the_same_payload_through(self):
        payload = {"id": "1", "config": {"alias": "Dusk"}}
        with enable_writes():
            with self.assertRaises(guards.ConfirmationRequired) as ctx:
                guards.authorise("upsert_automation", payload=payload)
            guards.authorise("upsert_automation", payload=payload, confirm_token=ctx.exception.token)

    def test_altering_the_payload_invalidates_the_token(self):
        """Tamper-evidence: the human approved a specific change."""
        with enable_writes():
            with self.assertRaises(guards.ConfirmationRequired) as ctx:
                guards.authorise("upsert_automation", payload={"id": "1", "brightness": 10})
            with self.assertRaises(guards.GuardRejection) as rejected:
                guards.authorise("upsert_automation",
                                 payload={"id": "1", "brightness": 255},
                                 confirm_token=ctx.exception.token)
        self.assertIn("payload changed", str(rejected.exception))

    def test_tokens_are_single_use(self):
        payload = {"id": "1"}
        with enable_writes():
            with self.assertRaises(guards.ConfirmationRequired) as ctx:
                guards.authorise("upsert_automation", payload=payload)
            token = ctx.exception.token
            guards.authorise("upsert_automation", payload=payload, confirm_token=token)
            with self.assertRaises(guards.GuardRejection) as replayed:
                guards.authorise("upsert_automation", payload=payload, confirm_token=token)
        self.assertIn("unknown or has expired", str(replayed.exception))

    def test_token_is_not_transferable_between_actions(self):
        with enable_writes():
            with self.assertRaises(guards.ConfirmationRequired) as ctx:
                guards.authorise("upsert_automation", payload={"id": "1"})
            with self.assertRaises(guards.GuardRejection) as rejected:
                guards.authorise("delete_automation", payload={"id": "1"},
                                 confirm_token=ctx.exception.token)
        self.assertIn("issued for 'upsert_automation'", str(rejected.exception))

    def test_token_is_not_transferable_between_agents(self):
        with enable_writes():
            with self.assertRaises(guards.ConfirmationRequired) as ctx:
                guards.authorise("upsert_automation", payload={"id": "1"}, agent_id="butler")
            with self.assertRaises(guards.GuardRejection) as rejected:
                guards.authorise("upsert_automation", payload={"id": "1"},
                                 confirm_token=ctx.exception.token, agent_id="someone-else")
        self.assertIn("different agent", str(rejected.exception))

    def test_expired_tokens_are_rejected(self):
        with enable_writes():
            with self.assertRaises(guards.ConfirmationRequired) as ctx:
                guards.authorise("upsert_automation", payload={"id": "1"})
            token = ctx.exception.token
            future = time.time() + guards.CONFIRMATION_TTL_SECONDS + 1
            with patch.object(guards.time, "time", lambda: future):
                with self.assertRaises(guards.GuardRejection) as rejected:
                    guards.authorise("upsert_automation", payload={"id": "1"}, confirm_token=token)
        self.assertIn("expired", str(rejected.exception))

    def test_unknown_token_is_rejected(self):
        with enable_writes():
            with self.assertRaises(guards.GuardRejection):
                guards.authorise("upsert_automation", payload={"id": "1"},
                                 confirm_token="made-up-token")

    def test_auto_apply_actions_need_no_token(self):
        with enable_writes():
            guards.authorise("call_service", target="light.porch",
                             payload={"service_data": {"brightness": 200}})


class TestBlanketGrant(unittest.TestCase):
    def setUp(self):
        guards.reset_pending()

    def test_blanket_grant_is_refused_for_writes(self):
        with enable_writes():
            with self.assertRaises(guards.GuardRejection) as ctx:
                guards.authorise("call_service", target="light.porch", blanket_grant=True)
        self.assertIn("explicit per-action grants", str(ctx.exception))

    def test_blanket_refusal_precedes_confirmation(self):
        """It must refuse outright, not mint a token the human could approve."""
        with enable_writes():
            with self.assertRaises(guards.GuardRejection):
                guards.authorise("upsert_automation", payload={"id": "1"}, blanket_grant=True)
        self.assertEqual(len(guards._pending), 0)


if __name__ == "__main__":
    unittest.main()
