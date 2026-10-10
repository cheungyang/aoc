import json
import os
import sys
import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.yfinance import yfinance, _normalize_tickers


def _payload(result: str) -> dict:
    return json.loads(result.split("<payload>")[1].split("</payload>")[0])


def _hist(closes, volumes):
    idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=len(closes), freq="D")
    return pd.DataFrame({"Close": closes, "High": closes, "Low": closes, "Volume": volumes}, index=idx)


def _ticker(hist, earnings=None, currency="USD"):
    t = MagicMock()
    t.history.return_value = hist
    t.calendar = {"Earnings Date": [earnings]} if earnings else {}
    t.fast_info = {"currency": currency}
    return t


class TestYfinanceTool(unittest.TestCase):

    def test_normalize_tickers(self):
        self.assertEqual(_normalize_tickers(["aapl", " TSLA ", "$msft", "AAPL", ""]), ["AAPL", "TSLA", "MSFT"])
        self.assertEqual(_normalize_tickers("aapl, tsla"), ["AAPL", "TSLA"])
        self.assertEqual(_normalize_tickers(None), [])

    def test_empty_tickers_error(self):
        self.assertIn("non-empty list", yfinance.invoke({"tickers": []}))

    def test_too_many_tickers_error(self):
        self.assertIn("At most 50", yfinance.invoke({"tickers": [f"T{i}" for i in range(51)]}))

    def test_unknown_action(self):
        self.assertIn("Unknown action", yfinance.invoke({"action": "bogus", "tickers": ["AAPL"]}))

    def test_invalid_period(self):
        self.assertIn("Invalid period", yfinance.invoke({"action": "history", "tickers": ["AAPL"], "period": "7w"}))

    @patch("tools.yfinance.yf.Ticker")
    def test_quote_computes_metrics_and_isolates_errors(self, mock_ticker):
        earn = date.today() + timedelta(days=5)
        good = _ticker(_hist([100.0] * 20 + [100.0, 104.0], [1000] * 21 + [3000]), earnings=earn)
        bad = _ticker(pd.DataFrame())
        mock_ticker.side_effect = lambda s: good if s == "AAPL" else bad

        result = yfinance.invoke({"action": "quote", "tickers": ["aapl", "NOPE"]})
        data = _payload(result)

        self.assertEqual(data["count"], 2)
        aapl, nope = data["results"]
        self.assertEqual(aapl["symbol"], "AAPL")
        self.assertEqual(aapl["current_price"], 104.0)
        self.assertEqual(aapl["daily_movement_pct"], 4.0)
        self.assertEqual(aapl["avg_volume"], 1000)
        self.assertEqual(aapl["volume_ratio"], 3.0)
        self.assertEqual(aapl["days_to_earnings"], 5)
        self.assertEqual(aapl["next_earnings_date"], earn.isoformat())
        self.assertIn("error", nope)
        self.assertIn("Data unavailable for: NOPE", result)

    @patch("tools.yfinance.yf.Ticker")
    def test_history(self, mock_ticker):
        mock_ticker.return_value = _ticker(_hist([10.0, 11.0, 12.1], [1, 2, 3]))
        res = _payload(yfinance.invoke({"action": "history", "tickers": ["MSFT"], "period": "5d"}))["results"][0]
        self.assertEqual(res["period_change_pct"], 21.0)
        self.assertEqual([b["change_pct"] for b in res["bars"]], [None, 10.0, 10.0])
        self.assertFalse(res["bars_truncated"])

    @patch("tools.yfinance.yf.Ticker")
    def test_earnings(self, mock_ticker):
        past = pd.Timestamp.today().normalize() - pd.Timedelta(days=30)
        future = pd.Timestamp.today().normalize() + pd.Timedelta(days=10)
        df = pd.DataFrame(
            {"EPS Estimate": [1.5, 1.0], "Reported EPS": [float("nan"), 1.2], "Surprise(%)": [float("nan"), 20.0]},
            index=[future, past],
        )
        t = _ticker(_hist([1.0], [1]))
        t.calendar = {}
        t.get_earnings_dates.return_value = df
        mock_ticker.return_value = t

        res = _payload(yfinance.invoke({"action": "earnings", "tickers": ["NVDA"]}))["results"][0]
        self.assertEqual(res["days_to_earnings"], 10)
        self.assertEqual(len(res["recent"]), 1)
        self.assertEqual(res["recent"][0]["surprise_pct"], 20.0)


if __name__ == "__main__":
    unittest.main()
