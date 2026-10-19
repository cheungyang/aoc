"""agents/reward-travel/lib/money.py: money with its ISO code."""
import unittest
from decimal import Decimal

from agents.reward_travel.lib.money import dollars_as_usd, usd


class TestMoney(unittest.TestCase):

    def test_usd_to_the_cent(self):
        self.assertEqual(usd(Decimal("2400")), "2,400.00 USD")
        self.assertEqual(usd(Decimal("134.555")), "134.56 USD")

    def test_bare_dollars_read_as_usd(self):
        cases = {
            "$5 / mo": "5 USD / mo",
            "$15 / mo ($20 Dec)": "15 USD / mo (20 USD Dec)",
            "$10 transit, $12 grocery delivery": "10 USD transit, 12 USD grocery delivery",
            "$1,200.50 / yr": "1,200.50 USD / yr",
            "Variable": "Variable",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(dollars_as_usd(text), expected)

    def test_other_marks_are_left_as_written(self):
        for text in ("NT$500 / mo", "US$20", "2x$90", "€50 / quarter"):
            with self.subTest(text=text):
                self.assertEqual(dollars_as_usd(text), text)


if __name__ == "__main__":
    unittest.main()
