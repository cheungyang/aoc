"""agents/reward-travel/lib/award.py: CPP, value bands and the alert line."""
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from core.tooling import DataProblem, Report
from core.util.fx import FxError, Rate
from agents.reward_travel.lib.award import AwardValueError, award_value, band_note, cents_per_point, value_band

TODAY = date(2026, 10, 16)
LINE = Decimal("2.0")  # cpp_alert_above's default


class TestAwardValue(unittest.TestCase):

    def test_cpp_formula(self):
        # (cash − taxes) ÷ points × 100
        self.assertEqual(cents_per_point(75000, Decimal("134.56"), Decimal("2400")), Decimal("3.02"))
        self.assertEqual(cents_per_point(60000, Decimal("5.60"), Decimal("450")), Decimal("0.74"))

    def test_bands_follow_flight_search_and_the_alert_threshold(self):
        cases = {"1.29": "poor", "1.30": "fair", "2.00": "fair", "2.01": "good", "3.00": "good", "3.01": "exceptional"}
        for cpp, band in cases.items():
            with self.subTest(cpp=cpp):
                self.assertEqual(value_band(Decimal(cpp), LINE), band)

    def test_the_alert_line_moves_the_good_band(self):
        self.assertEqual(value_band(Decimal("2.40"), Decimal("2.5")), "fair")
        self.assertEqual(value_band(Decimal("2.51"), Decimal("2.5")), "good")
        self.assertEqual(band_note("fair", Decimal("2.5")), "1.3 to 2.5")
        self.assertEqual(band_note("good", LINE), "above 2.0: worth an alert")

    def test_usd_option(self):
        value = award_value(75000, "$134.56", 2400, alert_above=2.0, today=TODAY)
        self.assertEqual((value.cpp, value.band, value.alert, value.rates), (Decimal("3.02"), "exceptional", True, ()))
        self.assertEqual(value.note, "above 3.0")
        self.assertFalse(award_value(70000, "$5.60", 1000, alert_above=2.0, today=TODAY).alert)  # 1.42

    def test_alert_is_above_the_line_not_at_it(self):
        at_line = award_value(100000, "0 USD", 2000, alert_above=2.0, today=TODAY)  # exactly 2.00
        self.assertEqual((at_line.cpp, at_line.band, at_line.alert), (Decimal("2.00"), "fair", False))
        self.assertFalse(award_value(100000, "0 USD", 2400, alert_above=2.5, today=TODAY).alert)  # 2.40

    def test_iso_amounts_read_like_symbols(self):
        value = award_value(75000, "134.56 USD", "2,400 USD", alert_above=2.0, today=TODAY)
        self.assertEqual((value.taxes_usd, value.cash_usd, value.cpp), (Decimal("134.56"), Decimal("2400.00"), Decimal("3.02")))

    def test_foreign_taxes_are_converted_at_todays_rate(self):
        eur = Rate("EUR", Decimal("1.09"), "2026-10-16", "ECB")
        report = Report()
        with patch("agents.reward_travel.lib.award.usd_rate", return_value=eur) as rate:
            value = award_value(88000, "€123.45", "2,950", alert_above=2.0, today=TODAY, report=report)
        rate.assert_called_once_with("EUR", None, TODAY)
        self.assertEqual(value.taxes_usd, Decimal("134.56"))
        self.assertEqual(value.cpp, Decimal("3.20"))
        self.assertEqual(value.rates, (eur,))
        self.assertIn("taxes 123.45 EUR × 1.09 = 134.56 USD", report.traces)
        self.assertEqual([read.source for read in report.reads], ["exchange rate 1 EUR = 1.09 USD (ECB, 2026-10-16)"])

    def test_traces_show_the_inputs(self):
        report = Report()
        award_value(75000, "$134.56", 2400, alert_above=2.0, today=TODAY, report=report)
        self.assertEqual(report.traces, [
            "CPP 3.02 = (2,400.00 USD cash − 134.56 USD taxes) ÷ 75,000 points × 100",
            "band exceptional (above 3.0); alert: 3.02 > cpp_alert_above 2.0",
        ])

    def test_bad_inputs(self):
        for points, taxes, cash in ((0, "$5", 100), (75000, "N/A", 100), (75000, "-$5", 100), (75000, "$5", 0), (7.5, "$5", 100)):
            with self.subTest(points=points, taxes=taxes, cash=cash), self.assertRaises(AwardValueError):
                award_value(points, taxes, cash, alert_above=2.0, today=TODAY)

    def test_unknown_taxes_point_at_the_trip(self):
        for taxes in ("N/A", "56.20 (currency not given, shown as '$')"):
            with self.subTest(taxes=taxes), self.assertRaises(AwardValueError) as caught:
                award_value(75000, taxes, 2400, alert_above=2.0, today=TODAY)
            self.assertIn("total_taxes", str(caught.exception))

    def test_no_rate_is_missing_data_not_a_guess(self):
        report = Report()
        with patch("agents.reward_travel.lib.award.usd_rate", side_effect=FxError("No USD rate for EUR today")):
            with self.assertRaises(DataProblem):
                award_value(75000, "€123.45", 2400, alert_above=2.0, today=TODAY, report=report)
        self.assertEqual(report.status, "failed")
        self.assertEqual(report.issue_lines(), ["exchange rates (ECB, currency-api): No USD rate for EUR today"])


if __name__ == "__main__":
    unittest.main()
