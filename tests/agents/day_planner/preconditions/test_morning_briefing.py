import json
import os
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from core.scheduler.spec import Findings, PromptSchedule, ScheduleContext
from core.util.config import Config
from tests.agents.day_planner import PlanningCase
from agents.day_planner.preconditions.morning_briefing import MorningBriefing

ZONE = ZoneInfo("America/Los_Angeles")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))


class TestMorningBriefing(PlanningCase):

    def setUp(self):
        super().setUp()
        patcher = mock.patch("core.util.time_util.get_local_timezone", return_value=ZONE)
        patcher.start()
        self.addCleanup(patcher.stop)

    def ctx(self, when):
        return ScheduleContext(schedule_id="day-planner:morning-plan", agent_id="day-planner", now=when.timestamp())

    def scan(self):
        return MorningBriefing({"type": "morning_briefing"})

    def test_the_morning_plan_declares_it(self):
        from core.scheduler.registry import load_agent_schedules

        with open(os.path.join(ROOT, "agents", "day-planner", "agent.json"), encoding="utf-8") as f:
            config = json.load(f)
        specs = {s.schedule_id: s for s in load_agent_schedules("day-planner", config).specs}
        self.assertIsInstance(specs["day-planner:morning-plan"].precondition, MorningBriefing)

    def test_always_runs_with_the_briefing_as_findings(self):
        self.task("- [ ] Pay the water bill every 15th of the month ⏫ 🔁 every month  📅 2026-10-15")
        decision = self.scan().evaluate(self.ctx(datetime(2026, 10, 16, 6, 0, tzinfo=ZONE)))
        self.assertTrue(decision)
        self.assertEqual(decision.reason, "morning briefing for Fri 2026-10-16")
        self.assertIsInstance(decision.detail, Findings)
        self.assertEqual(decision.detail.source, "morning_briefing")
        self.assertTrue(decision.detail.text.startswith("Fri 2026-10-16 · ISO week 2026-W42"))
        self.assertIn("1 day overdue (due Thu 10-15)", decision.detail.text)
        # The scheduled run only asks questions; logging happens later, after `day_briefing`.
        self.assertNotIn("New-note text", decision.detail.text)

    def test_uses_the_local_date_of_the_tick(self):
        # 23:30 in Los Angeles is already the next day in UTC; the briefing is for the local day.
        decision = self.scan().evaluate(self.ctx(datetime(2026, 10, 15, 23, 30, tzinfo=ZONE)))
        self.assertEqual(decision.reason, "morning briefing for Thu 2026-10-15")

    def test_findings_end_up_in_the_prompt(self):
        spec = PromptSchedule(schedule_id="day-planner:morning-plan", agent_id="day-planner", cron="0 6 * * *",
                              prompt="Start the morning routine.", precondition=self.scan())
        ctx = self.ctx(datetime(2026, 10, 16, 6, 0, tzinfo=ZONE))
        prompt = spec.build_prompt(ctx, spec.has_work(ctx))
        self.assertTrue(prompt.startswith('Start the morning routine.\n\n<findings source="morning_briefing">\nFri 2026-10-16'))
        self.assertTrue(prompt.endswith("</findings>"))

    def test_a_broken_build_skips_the_run(self):
        with mock.patch("agents.day_planner.lib.briefing.build_briefing", side_effect=RuntimeError("disk gone")):
            decision = self.scan().evaluate(self.ctx(datetime(2026, 10, 16, 6, 0, tzinfo=ZONE)))
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "morning_briefing can't run: the check failed (RuntimeError: disk gone)")

    def test_what_it_couldnt_read_rides_along_in_the_findings(self):
        self.write("agents/day-planner/RULES.md", "- chore_lead_days: soon\n")
        os.remove(Config().projects_db_path)
        decision = self.scan().evaluate(self.ctx(datetime(2026, 10, 16, 6, 0, tzinfo=ZONE)))
        self.assertTrue(decision)
        issues = decision.detail.issues
        self.assertEqual(len(issues), 2)
        self.assertTrue(issues[0].startswith("pkm/agents/day-planner/RULES.md: line 1: 'chore_lead_days: soon' can't be read"))
        self.assertTrue(issues[1].endswith("projects.db: not found"))

    def test_chore_lead_days_comes_from_rules_md(self):
        self.task("- [ ] Water the plants 🔁 every week 📅 2026-10-21")  # 5 days away, no history
        self.write("agents/day-planner/RULES.md", "- chore_lead_days: 5\n")
        decision = self.scan().evaluate(self.ctx(datetime(2026, 10, 16, 6, 0, tzinfo=ZONE)))
        self.assertIn("- Water the plants: due in 5 days (Wed 10-21); no completion history, so flagged 5 days ahead",
                      decision.detail.text)
