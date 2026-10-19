import json
import os
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from core.scheduler.spec import Findings, Problems, PromptSchedule, ScheduleContext
from tests.agents.meal_planner import MEAL_LOGS, KitchenFilesCase
from agents.meal_planner.preconditions.dinner_checkin import DinnerCheckin

ZONE = ZoneInfo("America/Los_Angeles")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
TONIGHT = datetime(2026, 10, 16, 21, 0, tzinfo=ZONE)


class TestDinnerCheckin(KitchenFilesCase):

    def setUp(self):
        super().setUp()
        patcher = mock.patch("core.tooling.get_local_timezone", return_value=ZONE)  # where the scan reads it
        patcher.start()
        self.addCleanup(patcher.stop)

    def evaluate(self, when=TONIGHT):
        ctx = ScheduleContext(schedule_id="meal-planner:dinner-checkin", agent_id="meal-planner", now=when.timestamp())
        return DinnerCheckin({"type": "dinner_checkin"}).evaluate(ctx)

    def test_the_21_00_check_in_declares_it(self):
        from core.scheduler.registry import load_agent_schedules

        with open(os.path.join(ROOT, "agents", "meal-planner", "agent.json"), encoding="utf-8") as f:
            config = json.load(f)
        result = load_agent_schedules("meal-planner", config)
        self.assertEqual(result.rejections, [])
        spec = {s.schedule_id: s for s in result.specs}["meal-planner:dinner-checkin"]
        self.assertEqual(spec.cron, "0 21 * * *")
        self.assertIsInstance(spec.precondition, DinnerCheckin)

    def test_asks_when_tonights_dinner_is_not_logged(self):
        decision = self.evaluate()
        self.assertTrue(decision)
        self.assertEqual(decision.reason, "no dinner logged for 2026-10-16 yet")
        self.assertIsNone(decision.detail)  # the question needs no findings

    def test_skips_when_dinner_is_already_logged_today(self):
        self.write("MEAL_LOGS.md", MEAL_LOGS + "\n### 2026-10-16\n- **午餐**：水餃\n- **晚餐**：番茄炒蛋（扣減雞蛋 4 顆）\n")
        decision = self.evaluate()
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "dinner already logged for 2026-10-16 (晚餐：番茄炒蛋)")
        self.assertIsNone(decision.detail)

    def test_eating_out_counts_as_dinner(self):
        self.write("MEAL_LOGS.md", MEAL_LOGS + "\n### 2026-10-16\n- **晚餐**：外食（拉麵）\n")
        self.assertFalse(self.evaluate())

    def test_lunch_or_tonights_ideas_are_not_dinner(self):
        self.write("MEAL_LOGS.md", MEAL_LOGS + (
            "\n### 2026-10-16\n- **午餐**：水餃\n"
            "\n### 2026-10-16 晚餐提案 (Dinner Recommendations)\n- **提案一**：香煎鮭魚\n"))
        self.assertTrue(self.evaluate())

    def test_uses_the_local_date_of_the_tick(self):
        # 23:30 in Los Angeles is already the next day in UTC; dinner on the 15th counts for that night.
        self.write("MEAL_LOGS.md", MEAL_LOGS + "\n### 2026-10-15\n- **晚餐**：水餃\n")
        decision = self.evaluate(datetime(2026, 10, 15, 23, 30, tzinfo=ZONE))
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "dinner already logged for 2026-10-15 (晚餐：水餃)")

    def test_a_missing_meal_log_still_asks_and_says_so(self):
        os.remove(self.path("MEAL_LOGS.md"))
        decision = self.evaluate()
        self.assertTrue(decision)
        self.assertIsInstance(decision.detail, Findings)
        self.assertEqual(decision.detail.issues,
                         ("pkm/wallet/kitchen/MEAL_LOGS.md: file not found, so tonight's dinner can't be checked",))

    def test_lines_it_cannot_read_today_ride_along_with_the_question(self):
        self.write("MEAL_LOGS.md", MEAL_LOGS + "\n### 2026-10-16\n晚餐：牛肉麵\n")
        decision = self.evaluate()
        self.assertTrue(decision)
        self.assertEqual(decision.detail.issues, ("pkm/wallet/kitchen/MEAL_LOGS.md: 1 line not recognized",))
        spec = PromptSchedule(schedule_id="meal-planner:dinner-checkin", agent_id="meal-planner", cron="0 21 * * *",
                              prompt="Ask what they had for dinner.", precondition=DinnerCheckin({"type": "dinner_checkin"}))
        ctx = ScheduleContext(schedule_id=spec.schedule_id, agent_id="meal-planner", now=TONIGHT.timestamp())
        prompt = spec.build_prompt(ctx, spec.has_work(ctx))
        self.assertIn("1 line not recognized", prompt)

    def test_a_broken_read_skips_the_run_and_posts_the_problem(self):
        with mock.patch("agents.meal_planner.lib.kitchen_io.read_text", side_effect=PermissionError("denied")):
            decision = self.evaluate()
        self.assertFalse(decision)
        self.assertIsInstance(decision.detail, Problems)
        self.assertEqual(decision.reason, "dinner_checkin can't run: the check failed (PermissionError: denied)")


if __name__ == "__main__":
    unittest.main()
