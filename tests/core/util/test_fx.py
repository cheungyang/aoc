"""core/util/fx.py: money parsing and USD rates. Every HTTP call is mocked."""
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import requests

from core.util import fx
from core.util.fx import FxError, MoneyError, Rate, format_usd, parse_money, to_usd, usd_rate

TODAY = date(2026, 10, 16)


def response(status=200, payload=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    return r


class TestParseMoney(unittest.TestCase):

    def test_symbols_and_codes(self):
        cases = {
            "€123.45": (Decimal("123.45"), "EUR"),
            "123.45 EUR": (Decimal("123.45"), "EUR"),
            "CAD 45": (Decimal("45"), "CAD"),
            "$5.60": (Decimal("5.60"), "USD"),
            "NT$1,200": (Decimal("1200"), "TWD"),
            "¥12,000": (Decimal("12000"), "JPY"),
            "£250.00": (Decimal("250.00"), "GBP"),
            "chf 12.50": (Decimal("12.50"), "CHF"),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_money(text), expected)

    def test_signs_and_separators(self):
        self.assertEqual(parse_money("-$6.40"), (Decimal("-6.40"), "USD"))
        self.assertEqual(parse_money("$-6.40"), (Decimal("-6.40"), "USD"))
        self.assertEqual(parse_money("(15.99)"), (Decimal("-15.99"), None))
        self.assertEqual(parse_money("1.234,56 €"), (Decimal("1234.56"), "EUR"))
        self.assertEqual(parse_money("1,234.56"), (Decimal("1234.56"), None))
        self.assertEqual(parse_money("123,45"), (Decimal("123.45"), None))

    def test_numbers_stay_exact(self):
        self.assertEqual(parse_money(9.17, "USD"), (Decimal("9.17"), "USD"))
        self.assertEqual(parse_money(110), (Decimal("110"), None))

    def test_refuses_what_it_cant_read(self):
        for text in ("N/A", "450 kr", "€12 (taxes)", "1,2345", True):
            with self.subTest(text=text), self.assertRaises(MoneyError):
                parse_money(text)

    def test_format_usd(self):
        self.assertEqual(format_usd(Decimal("1234.5")), "$1,234.50")
        self.assertEqual(format_usd(Decimal("-6.4")), "-$6.40")
        self.assertEqual(format_usd(Decimal("3.4375")), "$3.44")


class TestUsdRate(unittest.TestCase):

    def setUp(self):
        fx.clear_cache()
        self.addCleanup(fx.clear_cache)
        patcher = patch("core.util.fx.requests.get")
        self.get = patcher.start()
        self.addCleanup(patcher.stop)

    def test_usd_needs_no_network(self):
        rate = usd_rate("usd", date(2026, 10, 4), TODAY)
        self.assertEqual((rate.usd, rate.source, rate.date), (Decimal(1), "par", "2026-10-04"))
        self.get.assert_not_called()

    def test_ecb_currency_crosses_the_euro_quotes(self):
        self.get.return_value = response(payload={"date": "2026-10-02", "rates": {"USD": 1.0945, "JPY": 161.25}})
        rate = usd_rate("JPY", date(2026, 10, 4), TODAY)
        url, kwargs = self.get.call_args[0][0], self.get.call_args[1]
        self.assertEqual(url, "https://api.frankfurter.dev/v1/2026-10-04")
        self.assertEqual(kwargs["params"], {"symbols": "USD,JPY"})
        self.assertEqual(rate.usd, Decimal("1.0945") / Decimal("161.25"))
        self.assertEqual((rate.date, rate.source), ("2026-10-02", "ECB"))
        self.assertEqual(to_usd(Decimal("12000"), rate), Decimal("81.45"))

    def test_euro_itself(self):
        self.get.return_value = response(payload={"date": "2026-10-16", "rates": {"USD": 1.0945}})
        rate = usd_rate("EUR", None, TODAY)
        self.assertEqual(self.get.call_args[0][0], "https://api.frankfurter.dev/v1/latest")
        self.assertEqual(self.get.call_args[1]["params"], {"symbols": "USD"})
        self.assertEqual(rate.usd, Decimal("1.0945"))

    def test_twd_comes_from_currency_api_and_walks_back_over_a_missing_day(self):
        self.get.side_effect = [
            response(404),
            response(payload={"date": "2026-08-12", "twd": {"usd": 0.031274}}),
        ]
        rate = usd_rate("TWD", date(2026, 8, 13), TODAY)
        urls = [c[0][0] for c in self.get.call_args_list]
        self.assertEqual(urls, [
            "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@2026-08-13/v1/currencies/twd.json",
            "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@2026-08-12/v1/currencies/twd.json",
        ])
        self.assertEqual((rate.usd, rate.date, rate.source), (Decimal("0.031274"), "2026-08-12", "currency-api"))
        self.assertEqual(rate.describe(), "1 TWD = 0.031274 USD (currency-api, 2026-08-12)")

    def test_currency_api_mirror_when_jsdelivr_is_down(self):
        self.get.side_effect = [
            requests.ConnectionError("down"),
            response(payload={"date": "2026-10-16", "twd": {"usd": 0.0312}}),
        ]
        rate = usd_rate("TWD", None, TODAY)
        self.assertEqual(self.get.call_args[0][0], "https://latest.currency-api.pages.dev/v1/currencies/twd.json")
        self.assertEqual(rate.usd, Decimal("0.0312"))

    def test_falls_back_from_frankfurter_to_currency_api(self):
        self.get.side_effect = [response(503), response(payload={"date": "2026-10-16", "gbp": {"usd": 1.27}})]
        self.assertEqual(usd_rate("GBP", None, TODAY).source, "currency-api")

    def test_an_ecb_rate_3_days_old_still_counts(self):
        # Easter Sunday: the ECB's last rate is Thursday's (no rate on Good Friday).
        self.get.return_value = response(payload={"date": "2026-04-02", "rates": {"USD": 1.08}})
        rate = usd_rate("EUR", date(2026, 4, 5), TODAY)
        self.assertEqual((rate.date, rate.source), ("2026-04-02", "ECB"))

    def test_an_ecb_rate_more_than_3_days_old_falls_through_to_currency_api(self):
        # Easter Monday: the ECB publishes nothing, so its last rate is Thursday's, 4 days back.
        self.get.side_effect = [
            response(payload={"date": "2026-04-02", "rates": {"USD": 1.08}}),
            response(payload={"date": "2026-04-06", "eur": {"usd": 1.0812}}),
        ]
        rate = usd_rate("EUR", date(2026, 4, 6), TODAY)
        self.assertEqual(self.get.call_args[0][0],
                         "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@2026-04-06/v1/currencies/eur.json")
        self.assertEqual((rate.usd, rate.date, rate.source), (Decimal("1.0812"), "2026-04-06", "currency-api"))

    def test_the_latest_ecb_rate_is_held_to_today(self):
        # The Tuesday after Easter, before the ECB publishes: its latest rate is 5 days old.
        self.get.side_effect = [
            response(payload={"date": "2026-04-02", "rates": {"USD": 1.08}}),
            response(payload={"date": "2026-04-07", "eur": {"usd": 1.0815}}),
        ]
        rate = usd_rate("EUR", None, date(2026, 4, 7))
        self.assertEqual(self.get.call_args[0][0],
                         "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/eur.json")
        self.assertEqual((rate.date, rate.source), ("2026-04-07", "currency-api"))

    def test_an_ecb_rate_with_no_date_doesnt_count(self):
        self.get.side_effect = [
            response(payload={"rates": {"USD": 1.09}}),
            response(payload={"date": "2026-10-02", "eur": {"usd": 1.0901}}),
        ]
        self.assertEqual(usd_rate("EUR", date(2026, 10, 2), TODAY).source, "currency-api")

    def test_no_rate_within_3_days_says_so(self):
        self.get.side_effect = [response(payload={"date": "2026-04-02", "rates": {"USD": 1.08}})] + [response(404)] * 4
        with self.assertRaises(FxError) as caught:
            usd_rate("EUR", date(2026, 4, 6), TODAY)
        self.assertEqual(str(caught.exception),
                         "No USD rate for EUR on 2026-04-06 or the 3 days before: "
                         "ECB's EUR rate is from 2026-04-02, more than 3 days before 2026-04-06; "
                         "currency-api has no EUR file from 2026-04-03 to 2026-04-06")

    def test_no_source_answers_is_a_clear_error(self):
        self.get.side_effect = requests.ConnectionError("offline")
        with self.assertRaises(FxError) as caught:
            usd_rate("EUR", date(2026, 10, 1), TODAY)
        message = str(caught.exception)
        self.assertIn("No USD rate for EUR on 2026-10-01 or the 3 days before: ", message)
        self.assertIn("ECB unreachable", message)
        self.assertIn("currency-api unreachable", message)

    def test_cached_per_date_and_currency(self):
        self.get.return_value = response(payload={"date": "2026-10-02", "rates": {"USD": 1.09}})
        usd_rate("EUR", date(2026, 10, 2), TODAY)
        usd_rate("EUR", date(2026, 10, 2), TODAY)
        self.assertEqual(self.get.call_count, 1)
        usd_rate("EUR", date(2026, 10, 1), TODAY)
        self.assertEqual(self.get.call_count, 2)

    def test_tomorrow_is_a_receipt_from_ahead_of_us_and_later_is_refused(self):
        self.get.return_value = response(payload={"date": "2026-10-16", "rates": {"USD": 1.09}})
        usd_rate("EUR", date(2026, 10, 17), TODAY)
        self.assertTrue(self.get.call_args[0][0].endswith("/latest"))
        with self.assertRaises(FxError):
            usd_rate("EUR", date(2026, 10, 18), TODAY)

    def test_bad_code(self):
        with self.assertRaises(FxError):
            usd_rate("euro", None, TODAY)

    def test_rate_round_trip(self):
        self.assertEqual(to_usd(Decimal("110"), Rate("TWD", Decimal("0.03127"), "2026-08-13", "x")), Decimal("3.44"))


if __name__ == "__main__":
    unittest.main()
