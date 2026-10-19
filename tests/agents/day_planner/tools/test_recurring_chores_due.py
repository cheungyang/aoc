import os
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from core.util.config import Config
from tests.agents.day_planner import PlanningCase
from tests.helpers import envelope, execution_context
from agents.day_planner.tools.recurring_chores_due import recurring_chores_due

NOW = datetime(2026, 10, 16, 7, 30, tzinfo=ZoneInfo("America/Los_Angeles"))


class TestRecurringChoresDueTool(PlanningCase):

    def setUp(self):
        super().setUp()
        self.now = mock.patch("agents.day_planner.tools.recurring_chores_due.get_local_now", return_value=NOW)
        self.now.start()
        self.addCleanup(self.now.stop)

    def call(self, **args):
        with execution_context(agent_id="day-planner"):
            return envelope(recurring_chores_due.invoke(args))

    def test_needs_an_agent_run(self):
        reply = envelope(recurring_chores_due.invoke({}))
        self.assertEqual(reply["status"], "failed")
        self.assertIn("no active execution context", reply["errors"])

    def test_relays_the_computed_lines(self):
        self.task("- [ ] Pay the water bill every 15th of the month ⏫ 🔁 every month  📅 2026-10-15")
        self.task("- [x] Pay the water bill every 5th of the month (summer) ⏫ 🔁 every month 📅 2026-08-22 ✅ 2026-09-09")
        reply = self.call()
        self.assertEqual((reply["status"], reply["issues"], reply["errors"]), ("ok", "None", "None"))
        self.assertEqual(reply["payload"], "Overdue:\n- Pay the water bill every 15th of the month ⏫: 1 day overdue "
                                           "(due Thu 10-15); usually done 18 days late (1 past completion)\n"
                                           "Due today: none\nDue soon: none")

    def test_within_days_lists_chores_still_before_their_window(self):
        self.task("- [ ] Book piano lessons monthly 🔽 ⏳ 2026-10-21 🔁 every month")
        flagged = self.call()["payload"]
        week = self.call(within_days=7)["payload"]
        self.assertIn("Quiet until their usual window: 1 (next: Book piano lessons monthly, from Sun 10-18)", flagged)
        self.assertIn("Also due within 7 days, before their usual window:\n"
                      "- Book piano lessons monthly 🔽: scheduled in 5 days (Wed 10-21)", week)

    def test_a_chore_whose_start_date_is_in_the_future_isnt_due_yet(self):
        # Due in 2 days with no history, it would be flagged from Thu 10-15, but it starts on Sat 10-17.
        self.task("- [ ] Use the $30 travel credit ⏫ 🔁 every 6 months 🛫 2026-10-17 📅 2026-10-18")
        reply = self.call()
        self.assertIn("Due soon: none", reply["payload"])
        self.assertIn("Quiet until their usual window: 1 (next: Use the $30 travel credit, from Sat 10-17)",
                      reply["payload"])
        with mock.patch("agents.day_planner.tools.recurring_chores_due.get_local_now",
                        return_value=NOW + timedelta(days=1)):
            reply = self.call()
        self.assertIn("Due soon:\n- Use the $30 travel credit ⏫: due in 1 day (Sun 10-18); no completion history, "
                      "so flagged 3 days ahead", reply["payload"])

    def test_chore_lead_days_comes_from_rules_md(self):
        self.task("- [ ] Water the plants 🔁 every week 📅 2026-10-21")  # 5 days away, no history
        self.assertIn("Due soon: none", self.call()["payload"])
        self.write("agents/day-planner/RULES.md", "- chore_lead_days: 5  # flag new chores earlier\n")
        self.assertIn("Due soon:\n- Water the plants: due in 5 days (Wed 10-21); no completion history, "
                      "so flagged 5 days ahead", self.call()["payload"])

    def test_a_date_that_isnt_real_comes_back_raw(self):
        self.task("- [ ] Pay the rent 🔁 every month 📅 2026-02-30")
        self.task("- [ ] Water the plants 🔁 every week 📅 2026-10-16")
        reply = self.call()
        self.assertEqual(reply["status"], "partial")
        self.assertIn("tasks.db: 1 line not recognized\n    Pay the rent 🔁 every month 📅 2026-02-30", reply["issues"])
        self.assertIn("Due today:\n- Water the plants: due today", reply["payload"])

    def test_explain_shows_each_window(self):
        self.task("- [ ] Water the plants 🔁 every week 📅 2026-10-18")
        reply = self.call(explain=True)
        self.assertIn("Read:\n- ", reply["explain"])
        self.assertIn("tasks.db (modified ", reply["explain"])
        self.assertIn("Water the plants: due 2026-10-18; no completion history, so chore_lead_days (3) "
                      "→ opens 2026-10-15; soon on 2026-10-16", reply["explain"])

    def test_a_missing_cache_fails_and_isnt_created(self):
        os.remove(Config().tasks_db_path)
        reply = self.call()
        self.assertEqual(reply["status"], "failed")
        self.assertIn("tasks.db: not found (required)", reply["issues"])
        self.assertEqual(reply["payload"], "")
        self.assertFalse(os.path.exists(Config().tasks_db_path))
