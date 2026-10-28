"""agents/reward-travel/lib/credits.py: each credit's window, flags, and file ages."""
import os
import unittest
from datetime import date

from core.tooling import Report, load_rules
from tests.agents.reward_travel import CardFilesCase
from agents.reward_travel.lib.credits import Thresholds, credit_windows, flag_on, newly_flagged, stage_on, thresholds
from agents.reward_travel.lib.periods import current_period
from agents.reward_travel.lib.rules import RULES

DEFAULTS = Thresholds(behind_after_pct=50, lapse_days={"monthly": 7, "quarterly": 14, "semiannual": 21, "annual": 30},
                      stale_after_months=3)
AUTO_ROW = "| **Streaming Credit** *(Example Premium)* | $9 / mo | Auto | • Auto (Example TV) |\n"


class TestCreditWindows(CardFilesCase):

    def report(self, today, limits=DEFAULTS):
        return credit_windows(self.folder, today, limits, Report())

    def by_name(self, today, limits=DEFAULTS):
        return {w.name: w for w in self.report(today, limits).windows}

    def test_mid_october(self):
        windows = self.by_name(date(2026, 10, 16))
        coffee = windows["Sam's Coffee Credit (Example Dining)"]
        self.assertEqual((coffee.period.label, coffee.days_left, coffee.status, coffee.flag),
                         ("Oct 2026", 15, "not logged", "behind"))
        airline = windows["Example Premium Airline Credit"]
        self.assertEqual((airline.status, airline.remaining, airline.days_left, airline.flag),
                         ("partly used", "135 USD", 76, "behind"))
        self.assertEqual(windows["Dining Credit (Example Dining)"].flag, "behind")
        fitness = windows["Fitness Credit (Example Premium)"]
        self.assertEqual((fitness.status, fitness.flag), ("unused", None))
        self.assertEqual(windows["Hotel Credit (Luxury/Boutique)"].status, "used")

    def test_a_cardmember_year_has_a_status_and_no_dates(self):
        travel = self.by_name(date(2026, 10, 16))["Example Travel Card Credit"]
        self.assertEqual((travel.status, travel.period, travel.days_left, travel.flag), ("used", None, None, None))
        self.assertEqual(travel.notes, ("Next credit available after Jul 2027 renewal.",))

    def test_flags_at_the_edges(self):
        windows = self.by_name(date(2026, 10, 15))
        self.assertIsNone(windows["Sam's Coffee Credit (Example Dining)"].flag)  # 15 of 31 days: not yet half
        windows = self.by_name(date(2026, 10, 24))
        self.assertEqual(windows["Sam's Coffee Credit (Example Dining)"].flag, "lapsing")  # 7 days left
        windows = self.by_name(date(2026, 12, 10))
        self.assertEqual(windows["Dining Credit (Example Dining)"].flag, "lapsing")  # 21 days left
        self.assertEqual(windows["Example Premium Airline Credit"].flag, "lapsing")  # 21 of 30

    def test_logging_use_clears_the_flag(self):
        self.write("statement_credits.md", self.statement().replace(
            "• Jun 2026: Redeemed (2026-06-15) |", "• Jun 2026: Redeemed (2026-06-15)<br>• Oct 2026: Redeemed (2026-10-12) |"))
        coffee = self.by_name(date(2026, 10, 24))["Sam's Coffee Credit (Example Dining)"]
        self.assertEqual((coffee.status, coffee.flag), ("used", None))

    def test_an_auto_credit_is_never_flagged(self):
        """A streaming credit, used by a subscription every month."""
        self.add_row(AUTO_ROW)
        for day in (date(2026, 10, 16), date(2026, 10, 30)):
            with self.subTest(day=day):
                streaming = self.by_name(day)["Streaming Credit (Example Premium)"]
                self.assertEqual((streaming.value, streaming.status, streaming.by, streaming.flag),
                                 ("9 USD / mo", "auto", "Example TV", None))
        summary = self.report(date(2026, 10, 30))
        self.assertNotIn("Streaming Credit (Example Premium)", [w.name for w in newly_flagged(summary, None, DEFAULTS)])

    def test_this_periods_entry_wins_over_auto(self):
        self.add_row(AUTO_ROW.replace("• Auto (Example TV) |", "• Auto (Example TV)<br>• Oct 2026: MISSED (Example TV cancelled) |"))
        streaming = self.by_name(date(2026, 10, 30))["Streaming Credit (Example Premium)"]
        self.assertEqual((streaming.status, streaming.by), ("missed", None))
        november = self.by_name(date(2026, 11, 2))["Streaming Credit (Example Premium)"]
        self.assertEqual(november.status, "auto")

    def test_most_urgent_first(self):
        windows = self.report(date(2026, 10, 24)).windows
        self.assertEqual([w.flag for w in windows[:3]], ["lapsing"] * 3)
        self.assertEqual(windows[-1].status, "used")

    def test_newly_flagged_reports_each_stage_once(self):
        report = self.report(date(2026, 10, 16))
        self.assertEqual(len(newly_flagged(report, None, DEFAULTS)), len(report.flagged))
        # On Oct 14 the half-year and annual credits were already behind; the monthly ones weren't.
        names = [w.name for w in newly_flagged(report, date(2026, 10, 14), DEFAULTS)]
        self.assertEqual(names, ["Combined Rideshare Credit (Example Premium + Dining)", "Example Air Monthly Credits",
                                 "Sam's Coffee Credit (Example Dining)"])
        self.assertEqual(newly_flagged(report, date(2026, 10, 16), DEFAULTS), [])
        later = self.report(date(2026, 10, 27))
        self.assertEqual({w.flag for w in newly_flagged(later, date(2026, 10, 22), DEFAULTS)}, {"lapsing"})
        # A run last month says nothing about this month's monthly credits; the annual
        # airline credit was already behind on Sep 29, in the same year.
        names = {w.name for w in newly_flagged(report, date(2026, 9, 29), DEFAULTS)}
        self.assertIn("Sam's Coffee Credit (Example Dining)", names)
        self.assertIn("Dining Credit (Example Dining)", names)
        self.assertNotIn("Example Premium Airline Credit", names)

    def test_flag_on_another_day(self):
        coffee = self.by_name(date(2026, 10, 16))["Sam's Coffee Credit (Example Dining)"]
        self.assertEqual([flag_on(coffee, date(2026, 10, d), DEFAULTS) for d in (1, 16, 24)], [None, "behind", "lapsing"])
        self.assertIsNone(flag_on(coffee, date(2026, 11, 2), DEFAULTS))

    def test_the_windows_follow_the_thresholds(self):
        later = Thresholds(behind_after_pct=60, lapse_days={**DEFAULTS.lapse_days, "monthly": 10}, stale_after_months=3)
        self.assertIsNone(self.by_name(date(2026, 10, 16), later)["Sam's Coffee Credit (Example Dining)"].flag)  # 52% < 60%
        self.assertEqual(self.by_name(date(2026, 10, 21), later)["Sam's Coffee Credit (Example Dining)"].flag, "lapsing")  # 10 left

    def test_stage_on(self):
        october = current_period("monthly", date(2026, 10, 16))
        self.assertEqual([stage_on(october, date(2026, 10, d), 50, 7) for d in (15, 16, 24)], [None, "behind", "lapsing"])
        self.assertEqual(stage_on(october, date(2026, 10, 1), 0, 7), "behind")  # 0%: behind from day one
        self.assertIsNone(stage_on(october, date(2026, 10, 23), 100, 7))  # 100%: never behind

    def test_file_ages_and_staleness(self):
        files = {f.name: f for f in self.report(date(2026, 10, 16)).files}
        self.assertEqual((files["statement_credits.md"].months_old, files["statement_credits.md"].stale), (2, False))
        files = {f.name: f for f in self.report(date(2026, 12, 2)).files}
        self.assertEqual((files["statement_credits.md"].months_old, files["statement_credits.md"].stale), (4, True))
        tighter = Thresholds(DEFAULTS.behind_after_pct, DEFAULTS.lapse_days, stale_after_months=1)
        files = {f.name: f for f in self.report(date(2026, 10, 16), tighter).files}
        self.assertTrue(files["statement_credits.md"].stale)

    def test_five_24_passes_through(self):
        self.assertEqual([p.count for p in self.report(date(2026, 10, 16)).five_24], [3, 1])

    def test_explain_traces_each_credit_and_file(self):
        report = Report(explain=True)
        credit_windows(self.folder, date(2026, 10, 24), DEFAULTS, report)
        self.assertIn("Sam's Coffee Credit (Example Dining): no entry for this period -> not logged; Oct 2026, day 24 of 31, "
                      "7 days left (lapse_days monthly 7, behind_after_pct 50) -> lapsing", report.traces)
        self.assertIn("statement_credits.md: Last Updated August 30, 2026 -> 2 months old; stale after 3 "
                      "(stale_after_months): no", report.traces)

    def statement(self):
        with open(f"{self.folder}/statement_credits.md", encoding="utf-8") as f:
            return f.read()

    def add_row(self, row):
        """Adds a row to the monthly credits table."""
        text = self.statement()
        anchor = "| **Example Air Monthly Credits** |"
        self.write("statement_credits.md", text.replace(anchor, row + anchor))


class TestThresholds(CardFilesCase):
    """RULES.md's values for the windows, read over lib/rules.py's defaults."""

    def rules(self, text=None):
        if text is not None:
            path = os.path.join(self.pkm, "agents", "reward-travel", "RULES.md")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        report = Report()
        limits = thresholds(load_rules("reward-travel", RULES, report), report, "RULES.md")
        return limits, report

    def test_the_defaults_are_todays_values(self):
        limits, report = self.rules()
        self.assertEqual(limits, DEFAULTS)
        self.assertEqual(report.status, "ok")

    def test_rules_md_overrides(self):
        limits, report = self.rules("- behind_after_pct: 60\n- lapse_days:\n  - monthly: 10\n  - Semi-Annual: 30\n"
                                    "- stale_after_months: 6\n")
        self.assertEqual(report.status, "ok")
        self.assertEqual(limits, Thresholds(60, {"monthly": 10, "quarterly": 14, "semiannual": 30, "annual": 30}, 6))

    def test_bad_values_keep_the_default_and_say_so(self):
        limits, report = self.rules("- behind_after_pct: 150\n- lapse_days:\n  - weekly: 2\n  - annual: -5\n")
        self.assertEqual(limits, DEFAULTS)
        self.assertEqual(report.issue_lines(), [
            "RULES.md: lapse_days annual: -5 is out of range (0 or more); using the default 30",
            "RULES.md: lapse_days: 'weekly' is no cycle (monthly, quarterly, semiannual, annual); ignored",
            "RULES.md: behind_after_pct: 150 is out of range (0 to 100); using the default 50",
        ])


if __name__ == "__main__":
    unittest.main()
