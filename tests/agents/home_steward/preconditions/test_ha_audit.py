import unittest
from unittest import mock

from core.scheduler.preconditions import build_precondition
from core.scheduler.spec import Findings, Problems
from tests.core.scheduler.preconditions import ctx
from agents.home_steward.lib.homeassistant import audit
from agents.home_steward.lib.homeassistant.audit import AuditNews
from agents.home_steward.lib.homeassistant.client import HomeAssistantError
from agents.home_steward.preconditions.ha_audit import FIRST_RUN_LOOKBACK_SECONDS, HaAudit

NEWS = AuditNews(
    summary="1 new error(s), 0 newly unavailable or unknown; ongoing: 149 unavailable, 3 recurring error(s)",
    findings="New problems since Thu 08:00.\nNew errors (1), newest first:\n- ERROR ring: down",
)
QUIET = AuditNews(
    summary="0 new error(s), 0 newly unavailable or unknown; ongoing: 149 unavailable, 3 recurring error(s)",
    findings="",
)


class TestHaAudit(unittest.TestCase):

    def test_is_home_stewards_own_type(self):
        self.assertIsInstance(build_precondition({"type": "ha_audit"}, agent_id="home-steward"), HaAudit)

    def evaluate(self, result, last=1_792_000_000.0):
        """Evaluates the declared scan with `audit.check` returning `result`, or raising it."""
        outcome = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
        context = ctx(last)
        with mock.patch.object(audit, "check", **outcome) as check:
            decision = build_precondition({"type": "ha_audit"}, agent_id="home-steward").evaluate(context)
        return decision, check, context

    def test_new_problems_run_the_audit_with_the_findings(self):
        decision, _, _ = self.evaluate(NEWS)
        self.assertTrue(decision)
        self.assertEqual(decision.reason, NEWS.summary)
        self.assertEqual(decision.detail, Findings("ha_audit", NEWS.findings))

    def test_new_means_since_the_last_successful_audit(self):
        _, check, _ = self.evaluate(NEWS, last=1_792_000_000.0)
        self.assertEqual(check.call_args.args[0], 1_792_000_000.0)

    def test_the_first_audit_looks_back_a_day(self):
        _, check, context = self.evaluate(NEWS, last=None)
        self.assertEqual(check.call_args.args[0], context.now - FIRST_RUN_LOOKBACK_SECONDS)

    def test_no_new_problems_skips_the_model_turn(self):
        decision, _, _ = self.evaluate(QUIET)
        self.assertFalse(decision)
        self.assertEqual(decision.reason, f"no new problems ({QUIET.summary})")

    def test_home_assistant_unreachable_skips_the_run(self):
        decision, _, _ = self.evaluate(ConnectionError("Could not open a WebSocket"))
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "ha_audit failed (ConnectionError: Could not open a WebSocket)")

    def test_the_clients_own_unreachable_error_is_only_logged(self):
        decision, _, _ = self.evaluate(client_error_from_a_dropped_connection())
        self.assertFalse(decision)
        self.assertIsNone(decision.detail)
        self.assertIn("ha_audit failed (HomeAssistantError: Could not reach Home Assistant", decision.reason)

    def test_a_rejected_token_is_posted(self):
        decision, _, _ = self.evaluate(HomeAssistantError("Home Assistant rejected the token (401)"))
        self.assertFalse(decision)
        self.assertEqual(decision.detail, Problems(
            "ha_audit", ("the check failed (HomeAssistantError: Home Assistant rejected the token (401))",)))


def client_error_from_a_dropped_connection():
    """HomeAssistantError as client.py raises it: `from None`, while handling the requests error."""
    import requests

    try:
        try:
            raise requests.ConnectionError("Max retries exceeded")
        except requests.RequestException:
            raise HomeAssistantError("Could not reach Home Assistant at https://ha.example/api/states") from None
    except HomeAssistantError as error:
        return error


if __name__ == "__main__":
    unittest.main()
