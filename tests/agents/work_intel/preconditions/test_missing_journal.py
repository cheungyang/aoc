"""missing_journal: the scan that gates work-intel's 08:00 nudge to paste the day's update."""
import json
import os
import unittest
from datetime import date, datetime
from unittest import mock

from core.scheduler.preconditions import PreconditionError, build_precondition
from core.scheduler.spec import Findings, Problems, PromptSchedule, ScheduleContext
from tests.agents.work_intel import ZONE, JournalsCase, journal_text
from agents.work_intel.preconditions.missing_journal import MissingJournal

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
MORNING = datetime(2026, 11, 12, 8, 0, tzinfo=ZONE)
TODAY = MORNING.date()


class TestDeclaration(unittest.TestCase):

    def test_the_08_00_nudge_declares_it(self):
        from core.scheduler.registry import load_agent_schedules

        with open(os.path.join(ROOT, "agents", "work-intel", "agent.json"), encoding="utf-8") as f:
            config = json.load(f)
        result = load_agent_schedules("work-intel", config)
        self.assertEqual(result.rejections, [])
        spec = {s.schedule_id: s for s in result.specs}["work-intel:journal-nudge"]
        self.assertEqual(spec.cron, "0 8 * * 1-5")
        self.assertIsInstance(spec.precondition, MissingJournal)

    def test_other_agents_cannot(self):
        with self.assertRaises(PreconditionError):
            build_precondition({"type": "missing_journal"}, agent_id="meal-planner")


class TestMissingJournal(JournalsCase):

    def evaluate(self, when=MORNING):
        ctx = ScheduleContext(schedule_id="work-intel:journal-nudge", agent_id="work-intel",
                              now=when.timestamp())
        return MissingJournal({"type": "missing_journal"}).evaluate(ctx)

    def test_nudges_while_todays_journal_is_missing(self):
        self.write_journal(date(2026, 11, 11))  # yesterday's doesn't count for today
        decision = self.evaluate()
        self.assertTrue(decision)
        self.assertEqual(decision.reason, "no journal entry for 2026-11-12 yet")
        self.assertEqual(decision.detail, Findings(
            "missing_journal", "Today's update goes in pkm/vault/journals/work/2026-11-12.md, which doesn't exist yet."))

    def test_skips_once_todays_update_is_in(self):
        self.write_journal(TODAY)
        decision = self.evaluate()
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "journal for 2026-11-12 already in (pkm/vault/journals/work/2026-11-12.md)")
        self.assertIsNone(decision.detail)

    def test_a_journal_with_only_its_front_matter_still_gets_the_nudge(self):
        self.write("2026-11-12.md", journal_text(TODAY))  # opened from the template, nothing pasted yet
        decision = self.evaluate()
        self.assertTrue(decision)
        self.assertEqual(decision.detail.text, "Today's update goes in pkm/vault/journals/work/2026-11-12.md, "
                                               "which holds only its front matter so far.")

    def test_uses_the_local_date_of_the_tick(self):
        # 17:00 in Los Angeles is already the next day in UTC; today's journal is still the local day's.
        self.write_journal(TODAY)
        self.assertFalse(self.evaluate(datetime(2026, 11, 12, 17, 0, tzinfo=ZONE)))
        self.assertTrue(self.evaluate(datetime(2026, 11, 13, 8, 0, tzinfo=ZONE)))

    def test_a_missing_journals_folder_rides_along_with_the_nudge(self):
        os.rmdir(self.folder)
        decision = self.evaluate()
        self.assertTrue(decision)
        self.assertEqual(decision.detail.issues,
                         ("pkm/vault/journals/work: folder not found, so today's journal can't be checked",))

    def test_the_nudge_starts_from_the_findings(self):
        spec = PromptSchedule(schedule_id="work-intel:journal-nudge", agent_id="work-intel",
                              cron="0 8 * * 1-5", prompt="Nudge Alva to paste the update.",
                              precondition=MissingJournal({"type": "missing_journal"}))
        ctx = ScheduleContext(schedule_id=spec.schedule_id, agent_id="work-intel", now=MORNING.timestamp())
        prompt = spec.build_prompt(ctx, spec.has_work(ctx))
        self.assertTrue(prompt.startswith("Nudge Alva to paste the update.\n\n<findings source=\"missing_journal\">"))
        self.assertIn("pkm/vault/journals/work/2026-11-12.md", prompt)

    def test_a_journal_that_cant_be_read_skips_the_run_and_posts_the_problem(self):
        with mock.patch("agents.work_intel.lib.journals.read_journal", side_effect=PermissionError("denied")):
            decision = self.evaluate()
        self.assertFalse(decision)
        self.assertIsInstance(decision.detail, Problems)
        self.assertEqual(decision.reason, "missing_journal can't run: the check failed (PermissionError: denied)")


if __name__ == "__main__":
    unittest.main()
