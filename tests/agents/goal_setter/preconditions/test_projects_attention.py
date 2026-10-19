import json
import os
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from core.scheduler.preconditions import PreconditionError, build_precondition
from core.scheduler.spec import Findings, Problems, ScheduleContext
from tests.agents.goal_setter.lib.coaching_fixture import CoachingPkm
from agents.goal_setter.preconditions.projects_attention import ProjectsAttention

LA = ZoneInfo("America/Los_Angeles")
FRIDAY_9AM = datetime(2026, 10, 16, 9, tzinfo=LA).timestamp()


def schedule_ctx():
    return ScheduleContext(schedule_id="goal-setter:coach-executing", agent_id="goal-setter", now=FRIDAY_9AM)


class TestDeclaration(unittest.TestCase):

    def test_goal_setter_declares_it_by_type(self):
        self.assertIsInstance(build_precondition({"type": "projects_attention"}, agent_id="goal-setter"), ProjectsAttention)

    def test_other_agents_cannot(self):
        with self.assertRaises(PreconditionError):
            build_precondition({"type": "projects_attention"}, agent_id="excursion-planner")


@patch("core.util.time_util.get_local_timezone", return_value=LA)
class TestScan(unittest.TestCase):

    def setUp(self):
        self.pkm = CoachingPkm()
        self.addCleanup(self.pkm.cleanup)
        self.pkm.use_as_config()
        self.scan = build_precondition({"type": "projects_attention"}, agent_id="goal-setter")

    def coach_everyone_this_week(self):
        for name in ("Finish a 10k by June 2026", "Guitar lessons", "Spanish lessons"):
            self.pkm.write(f"wiki/project-coaching/{name}.md", f"---\nproject_name: {name}\nlast_coached: 2026-10-14\n---\n")

    def test_fires_with_the_ranking_and_the_first_projects_context(self, _tz):
        decision = self.scan.evaluate(schedule_ctx())
        self.assertTrue(decision.has_work)
        self.assertEqual(decision.reason, "Guitar lessons: never coached; stalled: no dated completion, 2 open")
        self.assertIsInstance(decision.detail, Findings)
        self.assertEqual(decision.detail.source, "projects_attention")
        self.assertEqual(decision.detail.issues, ())
        lines = decision.detail.text.splitlines()
        self.assertTrue(lines[0].startswith("Ranked first: Guitar lessons ("))
        self.assertEqual(lines[1:4], [
            "Next in line:",
            "- Finish a 10k by EoY 2026 (last coached 23 days ago; stalled: nothing done in 105 days, 2 open)",
            "- Spanish lessons (never coached; stalled: nothing done in 39 days, 1 open; 1 overdue task; "
            "committed for 2025, not 2026)",
        ])
        context = json.loads(lines[4].removeprefix("Its project_context: "))
        self.assertEqual(context["project"]["name"], "Guitar lessons")
        self.assertEqual(context["coaching"]["dossier"], "pkm/wiki/project-coaching/Guitar lessons.md")
        self.assertEqual(context["open_tasks"][0], "⏫ Book a trial lesson")

    def test_skips_when_every_executing_project_was_coached_this_week(self, _tz):
        self.coach_everyone_this_week()
        decision = self.scan.evaluate(schedule_ctx())
        self.assertFalse(decision.has_work)
        self.assertEqual(decision.reason, "no executing project needs coaching (4 coached in the last 7 days)")

    def test_reads_rest_days_from_rules_md(self, _tz):
        self.coach_everyone_this_week()
        self.pkm.write_rules("- rest_days: 1\n")
        decision = self.scan.evaluate(schedule_ctx())
        self.assertTrue(decision.has_work)  # nobody rests; the 10k is still stalled
        self.assertTrue(decision.reason.startswith("Finish a 10k by EoY 2026: stalled: nothing done in 105 days"))

    def test_a_quiet_day_with_an_unreadable_line_posts_it(self, _tz):
        self.coach_everyone_this_week()
        self.pkm.write("vault/projects/Project Alpha launch.md", "### 2026-10-1 from Grace\nShort sync.\n")
        decision = self.scan.evaluate(schedule_ctx())
        self.assertFalse(decision.has_work)
        self.assertEqual(decision.reason, "no executing project needs coaching (4 coached in the last 7 days) (with issues)")
        self.assertIsInstance(decision.detail, Problems)
        self.assertEqual(decision.detail.issues, ("pkm/vault/projects/Project Alpha launch.md: 1 line not recognized",))

    def test_fails_closed_without_a_project_store(self, _tz):
        os.remove(self.pkm.projects_db)
        decision = self.scan.evaluate(schedule_ctx())
        self.assertFalse(decision.has_work)
        self.assertEqual(decision.reason, "projects_attention can't run: pkm/projects.db: not found; run its sync first")
        self.assertIsInstance(decision.detail, Problems)
        self.assertFalse(os.path.exists(self.pkm.projects_db))


if __name__ == "__main__":
    unittest.main()
