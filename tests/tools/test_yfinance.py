import json
import os
import sys
import unittest
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.util.local_time import get_local_now
from tools.yfinance import yfinance, _normalize_tickers, _quote


def _payload(result: str) -> dict:
    return json.loads(result.split("<payload>")[1].split("</payload>")[0])


def _today():
    """The tool counts days in the configured timezone, so the fixtures do too."""
    return get_local_now().date()


def _hist(closes, volumes, end=None):
    idx = pd.date_range(end=pd.Timestamp(end or _today()), periods=len(closes), freq="D")
    return pd.DataFrame({"Close": closes, "High": closes, "Low": closes, "Volume": volumes}, index=idx)


def _ticker(hist, earnings=None, currency="USD"):
    t = MagicMock()
    t.history.return_value = hist
    t.calendar = {"Earnings Date": [earnings]} if earnings else {}
    t.fast_info = {"currency": currency}
    return t


class TestYfinanceTool(unittest.TestCase):

    def test_normalize_tickers(self):
        self.assertEqual(_normalize_tickers(["ko", " XOM ", "$ibm", "KO", ""]), ["KO", "XOM", "IBM"])
        self.assertEqual(_normalize_tickers("ko, xom"), ["KO", "XOM"])
        self.assertEqual(_normalize_tickers(None), [])

    def test_empty_tickers_error(self):
        self.assertIn("non-empty list", yfinance.invoke({"tickers": []}))

    def test_too_many_tickers_error(self):
        self.assertIn("At most 50", yfinance.invoke({"tickers": [f"T{i}" for i in range(51)]}))

    def test_unknown_action(self):
        self.assertIn("Unknown action", yfinance.invoke({"action": "bogus", "tickers": ["KO"]}))

    def test_invalid_period(self):
        self.assertIn("Invalid period", yfinance.invoke({"action": "history", "tickers": ["KO"], "period": "7w"}))

    @patch("tools.yfinance.yf.Ticker")
    def test_quote_computes_metrics_and_isolates_errors(self, mock_ticker):
        earn = _today() + timedelta(days=5)
        good = _ticker(_hist([100.0] * 20 + [100.0, 104.0], [1000] * 21 + [3000]), earnings=earn)
        bad = _ticker(pd.DataFrame())
        mock_ticker.side_effect = lambda s: good if s == "KO" else bad

        result = yfinance.invoke({"action": "quote", "tickers": ["ko", "NOPE"]})
        data = _payload(result)

        self.assertEqual(data["count"], 2)
        ko, nope = data["results"]
        self.assertEqual(ko["symbol"], "KO")
        self.assertEqual((ko["current_price"], ko["previous_close"]), ("USD 104.00", "USD 100.00"))
        self.assertNotIn("currency", ko)
        self.assertEqual(ko["daily_movement_pct"], 4.0)
        self.assertEqual(ko["avg_volume"], 1000)
        self.assertEqual(ko["volume_ratio"], 3.0)
        self.assertEqual(ko["days_to_earnings"], 5)
        self.assertEqual(ko["next_earnings_date"], earn.isoformat())
        self.assertIn("error", nope)
        self.assertIn("<errors>Data unavailable for: NOPE</errors>", result)

    @patch("tools.yfinance.yf.Ticker")
    def test_prices_in_minor_units_become_main_units_and_a_missing_currency_is_named(self, mock_ticker):
        tickers = {"VOD.L": _ticker(_hist([1200.0, 1234.0], [1, 1]), currency="GBp"),
                   "ODD": _ticker(_hist([9.5, 10.0], [1, 1]), currency=None)}
        mock_ticker.side_effect = tickers.get

        result = yfinance.invoke({"action": "quote", "tickers": ["VOD.L", "ODD"]})

        prices = {r["symbol"]: (r["current_price"], r["previous_close"]) for r in _payload(result)["results"]}
        self.assertEqual(prices, {"VOD.L": ("GBP 12.34", "GBP 12.00"),
                                  "ODD": ("10.00 (currency unknown)", "9.50 (currency unknown)")})
        self.assertIn("<errors>No currency from Yahoo for: ODD; their prices carry no code</errors>", result)

    @patch("tools.yfinance.yf.Ticker")
    def test_quote_keeps_plain_numbers_for_the_agents_that_import_it(self, mock_ticker):
        mock_ticker.return_value = _ticker(_hist([100.0, 104.0], [1, 1]))
        quote = _quote("KO")
        self.assertEqual((quote["currency"], quote["current_price"], quote["previous_close"]), ("USD", 104.0, 100.0))

    @patch("tools.yfinance.yf.Ticker")
    def test_history(self, mock_ticker):
        mock_ticker.return_value = _ticker(_hist([10.0, 11.0, 12.1], [1, 2, 3]))
        res = _payload(yfinance.invoke({"action": "history", "tickers": ["IBM"], "period": "5d"}))["results"][0]
        self.assertEqual(res["period_change_pct"], 21.0)
        self.assertEqual((res["period_high"], res["period_low"]), ("USD 12.10", "USD 10.00"))
        self.assertEqual([b["close"] for b in res["bars"]], ["USD 10.00", "USD 11.00", "USD 12.10"])
        self.assertEqual([b["change_pct"] for b in res["bars"]], [None, 10.0, 10.0])
        self.assertFalse(res["bars_truncated"])
        self.assertNotIn("currency", res)

    @patch("tools.yfinance.yf.Ticker")
    def test_earnings(self, mock_ticker):
        past = pd.Timestamp(_today()) - pd.Timedelta(days=30)
        future = pd.Timestamp(_today()) + pd.Timedelta(days=10)
        df = pd.DataFrame(
            {"EPS Estimate": [1.5, 1.0], "Reported EPS": [float("nan"), 1.2], "Surprise(%)": [float("nan"), 20.0]},
            index=[future, past],
        )
        t = _ticker(_hist([1.0], [1]))
        t.calendar = {}
        t.get_earnings_dates.return_value = df
        mock_ticker.return_value = t

        res = _payload(yfinance.invoke({"action": "earnings", "tickers": ["CAT"]}))["results"][0]
        self.assertEqual(res["days_to_earnings"], 10)
        self.assertEqual(len(res["recent"]), 1)
        self.assertEqual(res["recent"][0]["surprise_pct"], 20.0)
        # Yahoo's earnings calendar names no currency for EPS.
        self.assertEqual((res["recent"][0]["eps_estimate"], res["recent"][0]["eps_reported"]),
                         ("1.00 (currency unknown)", "1.20 (currency unknown)"))


if __name__ == "__main__":
    unittest.main()
