"""agents/reward-travel/preconditions/credit_windows.py: the credit-windows schedule's gate."""
import os
import shutil
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from core.scheduler.preconditions import build_precondition
from core.scheduler.spec import Findings, Problems, ScheduleContext
from tests.agents.reward_travel import CardFilesCase
from agents.reward_travel.preconditions.credit_windows import CreditWindows

ZONE = ZoneInfo("America/Los_Angeles")


class TestCreditWindowsScan(CardFilesCase):

    def setUp(self):
        super().setUp()
        patcher = patch("core.tooling.get_local_timezone", return_value=ZONE)
        patcher.start()
        self.addCleanup(patcher.stop)

    def evaluate(self, day, last=None):
        now = datetime(2026, 10, day, 9, 0, tzinfo=ZONE)
        since = (now - timedelta(days=now.day - last)).timestamp() if last else None
        ctx = ScheduleContext(schedule_id="reward-travel:credit-windows", agent_id="reward-travel",
                              now=now.timestamp(), last_success_at=since)
        return CreditWindows({"type": "credit_windows"}).evaluate(ctx)

    def test_is_declared_by_its_type_for_reward_travel(self):
        self.assertIsInstance(build_precondition({"type": "credit_windows"}, agent_id="reward-travel"), CreditWindows)

    def test_first_run_reports_every_credit_that_needs_using(self):
        decision = self.evaluate(16)
        self.assertTrue(decision)
        self.assertEqual(decision.reason, "5 credit(s) need using (0 about to lapse)")
        self.assertIsInstance(decision.detail, Findings)
        self.assertEqual(decision.detail.issues, ())
        text = decision.detail.text
        self.assertTrue(text.startswith("Card credits that need using, as of 2026-10-16:"))
        self.assertIn("- Sam's Coffee Credit (Example Dining), 5 USD / mo: not logged in Oct 2026, 15 days left (ends 2026-10-31) [behind]", text)
        self.assertIn("Example Premium Airline Credit, 180 USD / yr: partly used (135 USD left) in 2026", text)
        self.assertIn("Card files last updated: portfolio_summary.md September 2026; statement_credits.md August 30, 2026.", text)
        self.assertNotIn("Fitness", text)

    def test_only_what_changed_since_the_last_run(self):
        decision = self.evaluate(16, last=14)
        self.assertEqual(decision.reason, "3 credit(s) need using (0 about to lapse)")
        self.assertNotIn("Airline", decision.detail.text)

    def test_quiet_when_nothing_changed(self):
        decision = self.evaluate(16, last=16)
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "no credit newly behind or lapsing (5 reported before)")

    def test_a_deadline_brings_a_credit_back(self):
        decision = self.evaluate(27, last=22)
        self.assertTrue(decision)
        self.assertIn("about to lapse", decision.reason)
        self.assertIn("[lapsing]", decision.detail.text)

    def test_an_auto_credit_never_comes_up(self):
        with open(os.path.join(self.folder, "statement_credits.md"), encoding="utf-8") as f:
            text = f.read()
        self.write("statement_credits.md", text.replace(
            "| **Example Air Monthly Credits** |",
            "| **Streaming Credit** *(Example Premium)* | $9 / mo | Auto | • Auto (Example TV) |\n"
            "| **Example Air Monthly Credits** |"))
        for day, last in ((16, None), (27, 22)):
            with self.subTest(day=day):
                decision = self.evaluate(day, last)
                self.assertNotIn("Streaming Credit", decision.detail.text)

    def test_the_windows_come_from_rules_md(self):
        path = os.path.join(self.pkm, "agents", "reward-travel", "RULES.md")
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as f:
            f.write("- behind_after_pct: 60\n")
        decision = self.evaluate(16)  # 60% of a month is day 19, of H2 Oct 19: only the annual credit is past it
        self.assertEqual(decision.reason, "1 credit(s) need using (0 about to lapse)")
        self.assertIn("Example Premium Airline Credit", decision.detail.text)
        self.assertNotIn("Sam's Coffee", decision.detail.text)

    def test_lines_it_cant_read_ride_along_with_the_findings(self):
        with open(os.path.join(self.folder, "statement_credits.md"), encoding="utf-8") as f:
            text = f.read()
        self.write("statement_credits.md", text.replace("• Jun 2026: Redeemed (2026-06-15) |",
                                                        "• Jun 2026: Redeemed (2026-06-15)<br>• Redeemed (2026-10-12) |"))
        decision = self.evaluate(16)
        self.assertTrue(decision)
        self.assertEqual(decision.detail.issues, ("pkm/wallet/credit-card/statement_credits.md: 1 line not recognized",))
        quiet = self.evaluate(16, last=16)
        self.assertFalse(quiet)
        self.assertIsInstance(quiet.detail, Problems)  # posted in the channel, not skipped quietly

    def test_no_card_folder_is_posted(self):
        shutil.rmtree(self.folder)
        decision = self.evaluate(16)
        self.assertFalse(decision)
        self.assertIsInstance(decision.detail, Problems)
        self.assertEqual(decision.reason, "credit_windows can't run: pkm/wallet/credit-card: folder not found")

    def test_a_broken_read_fails_closed(self):
        with patch("agents.reward_travel.lib.credits.read_card_files", side_effect=OSError("disk gone")):
            decision = self.evaluate(16)
        self.assertFalse(decision)
        self.assertIn("credit_windows can't run: the check failed (OSError: disk gone)", decision.reason)
