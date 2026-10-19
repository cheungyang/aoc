import unittest
from contextlib import closing
from datetime import date

from agents.day_planner.lib.recurring_chores import ChoresDue, assess, chores_due, format_chores, same_chore
from agents.day_planner.lib.rules import RULES
from agents.day_planner.lib.task_cache import connect_readonly
from core.tooling import Report
from core.util.config import Config
from tests.agents.day_planner import TODAY, PlanningCase

LEAD_DAYS = 3  # chore_lead_days' default


class TestRules(unittest.TestCase):

    def test_chore_lead_days_defaults_to_the_value_built_in_before_rules_md(self):
        self.assertEqual({rule.key: rule.default for rule in RULES}, {"chore_lead_days": 3})


class TestAssess(unittest.TestCase):
    """The window math on its own: leads are days early (negative: late)."""

    def judge(self, on, leads=(), start=None, kind="due", lead_days=LEAD_DAYS):
        return assess("Chore", "⏫", kind, on, leads, TODAY, lead_days, start=start)

    def test_window_opens_at_the_longest_lead(self):
        on = date(2026, 10, 25)  # 9 days away
        self.assertEqual(self.judge(on, leads=(11, 20, 21)).state, "soon")
        self.assertEqual(self.judge(on, leads=(11, 20, 21)).opens, date(2026, 10, 4))
        self.assertEqual(self.judge(on, leads=(3, 5)).state, "quiet")
        self.assertEqual(self.judge(on, leads=(9,)).state, "soon")  # opens today
        self.assertEqual(self.judge(on, leads=(8,)).state, "quiet")

    def test_no_history_uses_chore_lead_days(self):
        self.assertEqual(self.judge(date(2026, 10, 16 + LEAD_DAYS)).state, "soon")
        self.assertEqual(self.judge(date(2026, 10, 17 + LEAD_DAYS)).state, "quiet")
        self.assertEqual(self.judge(date(2026, 10, 21), lead_days=5).state, "soon")
        self.assertEqual(self.judge(date(2026, 10, 21), lead_days=4).state, "quiet")

    def test_overdue_and_today_whatever_the_history(self):
        late = self.judge(date(2026, 10, 15), leads=(-18,))
        self.assertEqual((late.state, late.days_left), ("overdue", -1))
        self.assertEqual(self.judge(TODAY, leads=(-5, -2)).state, "today")

    def test_always_late_history_stays_quiet_until_the_date(self):
        self.assertEqual(self.judge(date(2026, 10, 18), leads=(-13, -5)).state, "quiet")

    def test_never_flagged_before_its_start_date(self):
        chore = self.judge(date(2027, 11, 1), leads=(159, 517), start=date(2026, 11, 15))
        self.assertEqual((chore.state, chore.opens), ("quiet", date(2026, 11, 15)))


class TestSameChore(unittest.TestCase):

    def test_drifted_titles_on_the_same_rule_match(self):
        self.assertTrue(same_chore("Pay the water bill every 15th of the month ⏫ 🔁 every month 📅 2026-10-15",
                                   "Pay the water bill every 5th of the month (summer) ⏫ 🔁 every month "
                                   "📅 2026-08-22 ✅ 2026-09-09"))

    def test_equal_titles_match_whatever_the_rule(self):
        self.assertTrue(same_chore("Water the plants 🔁 every week", "water the plants! 🔁 every 2 weeks ✅ 2026-10-01"))

    def test_near_titles_on_another_rule_or_other_chores_dont(self):
        self.assertFalse(same_chore("Water the plants 🔁 every week", "Water the plant 🔁 every 2 weeks"))
        self.assertFalse(same_chore("$15 dining credit monthly 🔁 every month", "$10 movie credit monthly 🔁 every month"))


class TestChoresDue(PlanningCase):
    """Real-looking lines, parsed by the real task parser into a temp tasks.db."""

    def setUp(self):
        super().setUp()
        bills = "vault/projects/Bills & budget.md"
        perks = "vault/pages/Perks to use.md"
        # Overdue; its title drifted from the copy completed 18 days late.
        self.task("- [ ] Pay the water bill every 15th of the month ⏫ 🔁 every month  📅 2026-10-15", bills)
        self.task("- [x] Pay the water bill every 5th of the month (summer) ⏫ 🔁 every month 📅 2026-08-22 "
                  "✅ 2026-09-09 %% aoc_id 0a1b2c3d4e5f %%", bills)
        # Due soon: usually done 11-21 days early, due in 9 days.
        self.task("- [ ] $15 dining credit monthly, see the app #p/budget ⏫ 🔁 every month 📅 2026-10-25", perks)
        for due, done in (("2026-09-25", "2026-09-04"), ("2026-08-25", "2026-08-05"), ("2026-07-25", "2026-07-14")):
            self.task(f"- [x] $15 dining credit monthly, see the app #p/budget ⏫ 🔁 every month 📅 {due} ✅ {done}",
                      perks)
        # Dropped copies and other chores' completions don't count.
        self.task("- [-] $15 dining credit monthly, see the app 🔁 every month 📅 2026-06-25 ❌ 2026-06-01", perks)
        self.task("- [x] $10 movie credit monthly ⏫ 🔁 every month 📅 2026-10-25 ✅ 2026-09-01", perks)
        # Scheduled only, no history: quiet until 3 days before.
        self.task("- [ ] Book piano lessons monthly 🔽 ⏳ 2026-10-25 🔁 every month", "vault/projects/Learn piano.md")
        # Due today.
        self.task("- [ ] Water the plants 🔁 every week 📅 2026-10-16")
        # Not judged: no date at all.
        self.task("- [ ] Stretch 🔁 every day")
        # Not a chore.
        self.task("- [ ] Pay the plumber 📅 2026-10-16")

    def report(self, today=TODAY, lead_days=LEAD_DAYS, report=None):
        with closing(connect_readonly(Config().tasks_db_path)) as conn:
            return chores_due(conn, today, lead_days, report or Report())

    def test_sorts_chores_by_state(self):
        chores = self.report()
        self.assertEqual([c.title for c in chores.overdue], ["Pay the water bill every 15th of the month"])
        self.assertEqual([c.title for c in chores.due_today], ["Water the plants"])
        self.assertEqual([c.title for c in chores.soon], ["$15 dining credit monthly, see the app"])
        self.assertEqual([c.title for c in chores.quiet], ["Book piano lessons monthly"])
        self.assertEqual(chores.undated, ["Stretch"])

    def test_history_shapes_each_window(self):
        chores = self.report()
        self.assertEqual(chores.overdue[0].leads, (-18,))
        self.assertEqual(chores.soon[0].leads, (21, 20, 11))  # newest first; dropped copy ignored
        self.assertEqual(chores.quiet[0].leads, ())
        self.assertEqual(chores.quiet[0].opens, date(2026, 10, 22))

    def test_lines_are_ready_to_relay(self):
        text = format_chores(self.report())
        self.assertEqual(text.splitlines(), [
            "Overdue:",
            "- Pay the water bill every 15th of the month ⏫: 1 day overdue (due Thu 10-15); "
            "usually done 18 days late (1 past completion)",
            "Due today:",
            "- Water the plants: due today",
            "Due soon:",
            "- $15 dining credit monthly, see the app ⏫: due in 9 days (Sun 10-25); "
            "usually done 11–21 days early (3 past completions)",
            "Quiet until their usual window: 1 (next: Book piano lessons monthly, from Thu 10-22)",
            "Not judged (no due or scheduled date): Stretch",
        ])

    def test_a_later_day_moves_chores_along(self):
        chores = self.report(date(2026, 10, 23))
        self.assertEqual([c.title for c in chores.soon], ["$15 dining credit monthly, see the app",
                                                          "Book piano lessons monthly"])
        self.assertIn("scheduled in 2 days (Sun 10-25); no completion history, so flagged 3 days ahead",
                      format_chores(chores))

    def test_chore_lead_days_sets_the_window_without_history(self):
        chores = self.report(lead_days=9)
        self.assertEqual([c.title for c in chores.soon], ["$15 dining credit monthly, see the app",
                                                          "Book piano lessons monthly"])
        self.assertIn("Book piano lessons monthly 🔽: scheduled in 9 days (Sun 10-25); no completion history, "
                      "so flagged 9 days ahead", format_chores(chores))

    def test_a_chore_whose_start_date_is_in_the_future_isnt_due_yet(self):
        # Due in 2 days with no history: flagged from Thu 10-15, were it not for its start date, Sat 10-17.
        self.task("- [ ] Use the $30 travel credit ⏫ 🔁 every 6 months 🛫 2026-10-17 📅 2026-10-18")
        quiet = {c.title: c for c in self.report().quiet}
        self.assertEqual(quiet["Use the $30 travel credit"].opens, date(2026, 10, 17))
        self.assertIn("Use the $30 travel credit", [c.title for c in self.report(date(2026, 10, 17)).soon])

    def test_looking_ahead_lists_quiet_chores_due_within_the_days(self):
        lines = format_chores(self.report(), within_days=7).splitlines()
        self.assertNotIn("Also due within 7 days, before their usual window:", lines)  # 9 days away
        lines = format_chores(self.report(), within_days=9).splitlines()
        self.assertEqual(lines[-3:], [
            "Also due within 9 days, before their usual window:",
            "- Book piano lessons monthly 🔽: scheduled in 9 days (Sun 10-25)",
            "Not judged (no due or scheduled date): Stretch",
        ])

    def test_reads_and_windows_are_recorded_for_explain(self):
        report = Report(explain=True)
        self.report(report=report)
        self.assertEqual(report.status, "ok")
        self.assertEqual([(read.records, read.skipped) for read in report.reads], [(10, 0)])  # 5 pending, 5 done
        self.assertIn("$15 dining credit monthly, see the app: due 2026-10-25; days early at its last "
                      "3 completions (negative: late): 21, 20, 11; the most is 21 → opens 2026-10-04; "
                      "soon on 2026-10-16", report.traces)

    def test_a_date_that_isnt_real_is_returned_raw(self):
        self.task("- [ ] Pay the rent 🔁 every month 📅 2026-02-30")
        self.task("- [x] Water the plants 🔁 every week 📅 2026-02-31 ✅ 2026-02-28")
        report = Report()
        chores = self.report(report=report)
        self.assertEqual(report.status, "partial")
        self.assertEqual(report.issues[0].lines, ["Water the plants 🔁 every week 📅 2026-02-31 ✅ 2026-02-28",
                                                  "Pay the rent 🔁 every month 📅 2026-02-30"])
        self.assertEqual([c.title for c in chores.due_today], ["Water the plants"])


class TestNoChores(PlanningCase):

    def test_says_none_for_each_state(self):
        self.task("- [ ] Pay the plumber 📅 2026-10-16")
        with closing(connect_readonly(Config().tasks_db_path)) as conn:
            text = format_chores(chores_due(conn, TODAY, LEAD_DAYS, Report()))
        self.assertEqual(text, "Overdue: none\nDue today: none\nDue soon: none")

    def test_chores_due_holds_the_lead_days_it_used(self):
        self.assertEqual(ChoresDue(today=TODAY, lead_days=4).lead_days, 4)


if __name__ == "__main__":
    unittest.main()
