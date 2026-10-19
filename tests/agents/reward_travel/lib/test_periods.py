"""agents/reward-travel/lib/periods.py: credit periods and the tracker's period labels."""
import unittest
from datetime import date

from agents.reward_travel.lib.periods import current_period, parse_period


class TestCurrentPeriod(unittest.TestCase):

    def test_each_cycle(self):
        today = date(2026, 10, 16)
        cases = {
            "monthly": (date(2026, 10, 1), date(2026, 10, 31), "Oct 2026"),
            "quarterly": (date(2026, 10, 1), date(2026, 12, 31), "Q4 2026 (Oct–Dec)"),
            "semiannual": (date(2026, 7, 1), date(2026, 12, 31), "H2 2026 (Jul–Dec)"),
            "annual": (date(2026, 1, 1), date(2026, 12, 31), "2026"),
        }
        for cycle, (start, end, label) in cases.items():
            with self.subTest(cycle=cycle):
                period = current_period(cycle, today)
                self.assertEqual((period.start, period.end, period.label), (start, end, label))

    def test_february_and_the_first_half(self):
        self.assertEqual(current_period("monthly", date(2028, 2, 10)).end, date(2028, 2, 29))
        self.assertEqual(current_period("semiannual", date(2026, 6, 30)).end, date(2026, 6, 30))

    def test_unknown_cycle(self):
        with self.assertRaises(ValueError):
            current_period("weekly", date(2026, 10, 16))


class TestParsePeriod(unittest.TestCase):

    def dates(self, text):
        period = parse_period(text)
        return (period.start, period.end) if period else None

    def test_labels_the_tracker_uses(self):
        cases = {
            "Aug 2026: **Redeemed (2026-08-11)** 🏆": (date(2026, 8, 1), date(2026, 8, 31)),
            "September 2026: Redeemed": (date(2026, 9, 1), date(2026, 9, 30)),
            "2026 Q1: Redeemed (2026-03-21)": (date(2026, 1, 1), date(2026, 3, 31)),
            "Q4 2026: Pending": (date(2026, 10, 1), date(2026, 12, 31)),
            "2026 H2: **Active / Pending Use**": (date(2026, 7, 1), date(2026, 12, 31)),
            "2026: $45 used (seat upgrade). **$135 Remaining**.": (date(2026, 1, 1), date(2026, 12, 31)),
            "2026 Certificate Redeemed (2026-03-14).": (date(2026, 1, 1), date(2026, 12, 31)),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.dates(text), expected)

    def test_a_month_range_wins_over_the_quarter_number(self):
        self.assertEqual(self.dates("2026 Q2 (Jul–Sep): **Pending / In Progress**"), (date(2026, 7, 1), date(2026, 9, 30)))
        self.assertEqual(self.dates("2026 Q3 (Oct–Dec): Upcoming"), (date(2026, 10, 1), date(2026, 12, 31)))

    def test_a_cardmember_cycle_spans_years(self):
        period = parse_period("2026–2027 Cycle: Redeemed (2026-07-08)")
        self.assertTrue(period.cardmember)
        self.assertEqual((period.start.year, period.end.year), (2026, 2027))

    def test_entries_without_a_period(self):
        for text in ("Monthly Transit ($10)", "Next credit available after Jul 2027 renewal.",
                     "$100 partner hotel credit available.", "Example Hotel 2026 stay"):
            with self.subTest(text=text):
                self.assertIsNone(parse_period(text))


if __name__ == "__main__":
    unittest.main()
