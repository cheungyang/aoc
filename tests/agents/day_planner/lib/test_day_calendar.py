import unittest
from datetime import date

from agents.day_planner.lib.day_calendar import (
    iso_week_label,
    locale_week,
    past_days,
    relative_phrase,
    short_date,
    vault_week_names,
    week_start,
)


class TestWeeks(unittest.TestCase):

    def test_vault_weeks_start_on_sunday_and_count_from_the_week_of_january_first(self):
        self.assertEqual(week_start(date(2026, 10, 16)), date(2026, 10, 11))
        self.assertEqual(week_start(date(2026, 10, 11)), date(2026, 10, 11))
        self.assertEqual(locale_week(date(2026, 10, 16)), (2026, 42))
        # Sunday: the vault week has turned, the ISO week has not.
        self.assertEqual(locale_week(date(2026, 10, 18)), (2026, 43))
        self.assertEqual(iso_week_label(date(2026, 10, 18)), "2026-W42")
        # The week holding January 1st is week 1, even for its December days.
        self.assertEqual(locale_week(date(2025, 12, 28)), (2026, 1))
        self.assertEqual(locale_week(date(2026, 1, 3)), (2026, 1))
        self.assertEqual(locale_week(date(2026, 1, 4)), (2026, 2))
        self.assertEqual(locale_week(date(2026, 12, 27)), (2027, 1))
        self.assertEqual(locale_week(date(2026, 12, 26)), (2026, 52))

    def test_matches_the_journals_plugin_names_in_the_vault(self):
        # Real notes: 2026-W42 has journal-date 2026-10-11, 2026-W9 has 2026-02-22.
        self.assertEqual(vault_week_names(date(2026, 10, 16))[0], "2026-W42")
        self.assertEqual(vault_week_names(date(2026, 2, 22))[0], "2026-W9")
        self.assertIn("2026-W09", vault_week_names(date(2026, 2, 25)))

    def test_iso_label_is_zero_padded(self):
        self.assertEqual(iso_week_label(date(2026, 2, 25)), "2026-W09")


class TestDays(unittest.TestCase):
    TODAY = date(2026, 10, 16)

    def test_past_seven_days_end_yesterday(self):
        days = past_days(self.TODAY)
        self.assertEqual(len(days), 7)
        self.assertEqual(days[0], date(2026, 10, 9))
        self.assertEqual(days[-1], date(2026, 10, 15))

    def test_past_days_cross_month_and_year(self):
        self.assertEqual(past_days(date(2026, 1, 2), 3), [date(2025, 12, 30), date(2025, 12, 31), date(2026, 1, 1)])

    def test_short_date_adds_the_year_only_when_it_differs(self):
        self.assertEqual(short_date(date(2026, 10, 15), self.TODAY), "Thu 10-15")
        self.assertEqual(short_date(date(2027, 1, 4), self.TODAY), "Mon 2027-01-04")

    def test_relative_phrases(self):
        self.assertEqual(relative_phrase("due", date(2026, 10, 15), self.TODAY), "1 day overdue (due Thu 10-15)")
        self.assertEqual(relative_phrase("due", self.TODAY, self.TODAY), "due today")
        self.assertEqual(relative_phrase("due", date(2026, 10, 19), self.TODAY), "due in 3 days (Mon 10-19)")
        self.assertEqual(relative_phrase("scheduled", date(2026, 10, 1), self.TODAY), "scheduled 15 days ago (Thu 10-01)")
        self.assertEqual(relative_phrase("scheduled", self.TODAY, self.TODAY), "scheduled today")

    def test_day_counts_are_calendar_days_across_dst(self):
        # Clocks go back on Sun 2026-11-01 in Los Angeles; dates still count whole days.
        self.assertEqual(relative_phrase("due", date(2026, 11, 2), date(2026, 10, 31)), "due in 2 days (Mon 11-02)")


if __name__ == "__main__":
    unittest.main()
