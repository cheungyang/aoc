"""agents/reward-travel/tools/award_value.py: the CPP tool's reply."""
import json
import os
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import patch

from core.config.settings import Config
from core.util.fx import FxError, Rate
from tests.helpers import envelope, execution_context
from agents.reward_travel.tools.award_value import award_value


class TestAwardValueTool(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.pkm = tmp.name
        Config().pkm_dir = self.pkm
        self.addCleanup(setattr, Config(), "pkm_dir", None)

    def call(self, **args):
        with execution_context(agent_id="reward-travel"):
            return envelope(award_value.invoke(args))

    def test_usd_option(self):
        out = self.call(points=75000, taxes="$134.56", cash=2400)
        self.assertEqual((out["status"], out["issues"], out["errors"]), ("ok", "None", "None"))
        self.assertEqual(json.loads(out["payload"]), {
            "cpp": 3.02, "band": "exceptional", "note": "above 3.0",
            "alert": True, "points": 75000, "taxes": "134.56 USD", "cash": "2,400.00 USD",
        })

    def test_reports_the_exchange_rate_it_used(self):
        eur = Rate("EUR", Decimal("1.09"), "2026-10-16", "ECB")
        with patch("agents.reward_travel.lib.award.usd_rate", return_value=eur):
            data = json.loads(self.call(points=88000, taxes="€123.45", cash="$2,950")["payload"])
        self.assertEqual((data["cpp"], data["band"], data["alert"], data["taxes"]), (3.2, "exceptional", True, "134.56 USD"))
        self.assertEqual(data["fx"], ["1 EUR = 1.09 USD (ECB, 2026-10-16)"])

    def test_seats_aero_taxes_with_their_iso_code(self):
        cad = Rate("CAD", Decimal("0.73"), "2026-10-16", "ECB")
        with patch("agents.reward_travel.lib.award.usd_rate", return_value=cad) as rate:
            data = json.loads(self.call(points=75000, taxes="56.20 CAD", cash=2400)["payload"])
        rate.assert_called_once()
        self.assertEqual(rate.call_args[0][0], "CAD")
        self.assertEqual(data["taxes"], "41.03 USD")

    def test_the_alert_line_comes_from_rules_md(self):
        path = os.path.join(self.pkm, "agents", "reward-travel", "RULES.md")
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as f:
            f.write("- cpp_alert_above: 2.5\n")
        data = json.loads(self.call(points=100000, taxes="$0", cash=2400)["payload"])  # 2.40
        self.assertEqual((data["band"], data["note"], data["alert"]), ("fair", "1.3 to 2.5", False))

    def test_explain_lists_the_inputs(self):
        out = self.call(points=75000, taxes="$134.56", cash=2400, explain=True)
        self.assertIn("CPP 3.02 = (2,400.00 USD cash − 134.56 USD taxes) ÷ 75,000 points × 100", out["explain"])
        self.assertIn("alert: 3.02 > cpp_alert_above 2.0", out["explain"])

    def test_bad_inputs_are_errors_not_guesses(self):
        out = self.call(points=75000, taxes="N/A", cash=2400)
        self.assertEqual(out["status"], "failed")
        self.assertIn("total_taxes", out["errors"])

    def test_no_rate_is_failed_with_the_reason(self):
        with patch("agents.reward_travel.lib.award.usd_rate", side_effect=FxError("No USD rate for EUR today")):
            out = self.call(points=1, taxes="€1", cash=9)
        self.assertEqual(out["status"], "failed")
        self.assertIn("exchange rates (ECB, currency-api): No USD rate for EUR today (required)", out["issues"])
        self.assertEqual(out["payload"], "")

    def test_needs_an_agent_run(self):
        self.assertIn("no active execution context", award_value.invoke({"points": 1, "taxes": "$1", "cash": 9}))


if __name__ == "__main__":
    unittest.main()
