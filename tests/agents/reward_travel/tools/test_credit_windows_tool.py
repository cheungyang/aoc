"""agents/reward-travel/tools/credit_windows.py: the credit-window tool's reply."""
import json
import os
import shutil
from datetime import datetime
from unittest.mock import patch

from tests.agents.reward_travel import CardFilesCase
from tests.helpers import envelope, execution_context
from agents.reward_travel.tools import credit_windows as module
from agents.reward_travel.tools.credit_windows import credit_windows


class TestCreditWindowsTool(CardFilesCase):

    def setUp(self):
        super().setUp()
        now = patch.object(module, "get_local_now", return_value=datetime(2026, 10, 24, 9, 0))
        now.start()
        self.addCleanup(now.stop)

    def call(self, **args):
        with execution_context(agent_id="reward-travel"):
            return envelope(credit_windows.invoke(args))

    def test_decision_ready_rows(self):
        out = self.call()
        self.assertEqual((out["status"], out["issues"], out["errors"]), ("ok", "None", "None"))
        data = json.loads(out["payload"])
        self.assertEqual(data["today"], "2026-10-24")
        first = data["credits"][0]
        self.assertEqual(first, {
            "credit": "Combined Rideshare Credit (Example Premium + Dining)", "value": "15 USD / mo (20 USD Dec)",
            "period": "Oct 2026", "ends": "2026-10-31", "days_left": 7, "status": "not logged", "flag": "lapsing",
        })
        airline = next(c for c in data["credits"] if c["credit"] == "Example Premium Airline Credit")
        self.assertEqual((airline["remaining"], airline["flag"]), ("135 USD", "behind"))
        travel = next(c for c in data["credits"] if c["credit"].startswith("Example Travel"))
        self.assertEqual((travel["period"], travel["ends"], travel["status"]), ("cardmember year", None, "used"))
        self.assertEqual(travel["notes"], ["Next credit available after Jul 2027 renewal."])
        self.assertNotIn("flag", travel)
        self.assertEqual(data["five_24"][0], {"player": "Player 1 (You)", "count": 3,
                                              "status": "~3/24 (Example Air & Example Hotel in 2026)"})
        self.assertEqual(data["files"][1], {"file": "statement_credits.md", "last_updated": "August 30, 2026",
                                            "months_old": 2, "stale": False})

    def test_an_auto_credit_says_what_uses_it(self):
        text = self.statement().replace(
            "| **Example Air Monthly Credits** |",
            "| **Streaming Credit** *(Example Premium)* | $9 / mo | Auto | • Auto (Example TV) |\n"
            "| **Example Air Monthly Credits** |")
        self.write("statement_credits.md", text)
        data = json.loads(self.call()["payload"])
        streaming = next(c for c in data["credits"] if c["credit"].startswith("Streaming"))
        self.assertEqual(streaming, {"credit": "Streaming Credit (Example Premium)", "value": "9 USD / mo",
                                     "period": "Oct 2026", "ends": "2026-10-31", "days_left": 7, "status": "auto",
                                     "by": "Example TV"})

    def test_lines_it_cant_read_come_back_raw(self):
        self.write("statement_credits.md", self.statement().replace(
            "• Jun 2026: Redeemed (2026-06-15) |", "• Jun 2026: Redeemed (2026-06-15)<br>• Redeemed (2026-10-12) |"))
        out = self.call()
        self.assertEqual(out["status"], "partial")
        self.assertIn("- pkm/wallet/credit-card/statement_credits.md: 1 line not recognized\n"
                      "    Sam's Coffee Credit (Example Dining): Redeemed (2026-10-12)", out["issues"])
        self.assertTrue(json.loads(out["payload"])["credits"])  # the rest still answers

    def test_thresholds_come_from_rules_md(self):
        path = os.path.join(self.pkm, "agents", "reward-travel", "RULES.md")
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as f:
            f.write("- lapse_days:\n  - monthly: 5\n- stale_after_months: 1\n")
        data = json.loads(self.call()["payload"])
        self.assertEqual(data["credits"][0]["flag"], "behind")  # 7 days left, lapsing only at 5
        self.assertTrue(data["files"][1]["stale"])

    def test_explain_lists_the_files_and_the_inputs(self):
        out = self.call(explain=True)
        self.assertIn("- pkm/wallet/credit-card/statement_credits.md (modified ", out["explain"])
        self.assertIn("10 records)", out["explain"])
        self.assertIn("Sam's Coffee Credit (Example Dining): no entry for this period -> not logged; Oct 2026, day 24 of 31",
                      out["explain"])

    def test_no_credit_table_is_failed(self):
        os.remove(os.path.join(self.folder, "statement_credits.md"))
        out = self.call()
        self.assertEqual(out["status"], "failed")
        self.assertIn("pkm/wallet/credit-card: no statement-credit table", out["issues"])
        self.assertEqual(out["payload"], "")

    def test_no_card_folder_is_failed(self):
        shutil.rmtree(self.folder)
        out = self.call()
        self.assertEqual(out["status"], "failed")
        self.assertIn("pkm/wallet/credit-card: folder not found (required)", out["issues"])

    def statement(self):
        with open(os.path.join(self.folder, "statement_credits.md"), encoding="utf-8") as f:
            return f.read()
