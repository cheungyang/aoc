"""The market_screen run: quotes for the tickers, checked against the thresholds. Yahoo is mocked."""
import os
import shutil
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pandas as pd

from core.tooling import Report, ToolError
from core.config.settings import Config
from tests.tools.test_yfinance import _hist, _ticker, _today
from agents.market_scout.lib.quotes import screen_quotes
from agents.market_scout.lib.rules import RULES

DEFAULTS = {rule.key: rule.default for rule in RULES}


class MockedYahoo(unittest.TestCase):
    """Temp pkm lists and a yf.Ticker that serves `self.tickers` (a ticker, or a function of the call count)."""

    def setUp(self):
        self.pkm = tempfile.mkdtemp()
        Config().pkm_dir = self.pkm
        self.addCleanup(setattr, Config(), "pkm_dir", None)
        self.addCleanup(shutil.rmtree, self.pkm, True)
        os.makedirs(os.path.join(self.pkm, "wallet", "stock"))
        patcher = patch("tools.yfinance.yf.Ticker", side_effect=self._ticker_for)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.calls = {}
        self.tickers = {}

    def _ticker_for(self, symbol):
        self.calls[symbol] = self.calls.get(symbol, 0) + 1
        made = self.tickers.get(symbol, _ticker(pd.DataFrame()))
        return made if isinstance(made, MagicMock) else made(self.calls[symbol])

    def write(self, name, text):
        with open(os.path.join(self.pkm, "wallet", "stock", name), "w", encoding="utf-8") as f:
            f.write(text)

    def write_rules(self, text):
        folder = os.path.join(self.pkm, "agents", "market-scout")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "RULES.md"), "w", encoding="utf-8") as f:
            f.write(text)

    @staticmethod
    def moving(last, previous=100.0, volume=1000, earnings=None, end=None, currency="USD"):
        return _ticker(_hist([100.0] * 20 + [previous, last], [1000] * 21 + [volume], end=end),
                       earnings=earnings, currency=currency)


class TestScreenQuotes(MockedYahoo):

    def screen(self, followed, rules=DEFAULTS):
        self.report = Report(explain=True)
        return screen_quotes(followed, rules, self.report)

    def test_screens_the_tickers_crossed_first(self):
        self.tickers = {
            "PEP": self.moving(98.89),
            "IBM": self.moving(100.5, earnings=_today() + timedelta(days=5)),
            "XOM": self.moving(104.6, volume=2600),
        }

        data = self.screen([("PEP", "portfolio"), ("IBM", "portfolio"), ("XOM", "watchlist"), ("NOPE", "watchlist")])

        self.assertEqual([r["symbol"] for r in data["results"]], ["XOM", "IBM", "NOPE", "PEP"])
        xom, ibm, nope, pep = data["results"]
        self.assertEqual(xom, {
            "symbol": "XOM", "list": "watchlist", "crossed": ["move +4.6%", "volume 2.6x avg"],
            "price": "USD 104.60", "daily_movement_pct": 4.6, "volume_ratio": 2.6,
            "next_earnings_date": None, "days_to_earnings": None,
        })
        self.assertEqual(ibm["crossed"], ["earnings in 5 d"])
        self.assertEqual(nope, {"symbol": "NOPE", "list": "watchlist",
                                "error": "ValueError: No price data returned (invalid or delisted symbol?)"})
        self.assertEqual((pep["list"], pep["crossed"], pep["price"]), ("portfolio", [], "USD 98.89"))
        self.assertEqual((data["count"], data["crossed_any"]), (4, 2))
        self.assertEqual(data["thresholds"], {"move_pct": 3.0, "earnings_days": 7, "volume_ratio": 2.0})
        self.assertEqual(data["as_of_date"], _today().isoformat())
        self.assertTrue(data["as_of_today"])

    def test_a_ticker_without_data_is_an_issue_under_its_list(self):
        self.tickers = {"PEP": self.moving(100.0)}

        self.screen([("PEP", "portfolio"), ("NOPE", "watchlist")])

        self.assertEqual(self.report.status, "partial")
        self.assertEqual(self.report.issue_lines(), [
            "pkm/wallet/stock/watchlist.md: NOPE: no data from Yahoo Finance after a retry "
            "(ValueError: No price data returned (invalid or delisted symbol?))",
        ])

    def test_the_inputs_behind_each_number_are_traced(self):
        self.tickers = {"XOM": self.moving(104.6, volume=2600)}

        self.screen([("XOM", "watchlist"), ("NOPE", "watchlist")])

        [trace] = self.report.traces
        self.assertTrue(trace.startswith(f"XOM: close 104.6 USD on {_today().isoformat()}, previous close 100.0 -> move 4.6%; "))
        self.assertIn("volume 2600 over a 20-session average of 1000 -> 2.6x; no earnings date; ", trace)
        self.assertTrue(trace.endswith("crossed: move +4.6%, volume 2.6x avg"))
        self.assertEqual([(r.source, r.records) for r in self.report.reads], [("Yahoo Finance", 1)])

    def test_a_failed_quote_is_retried_once(self):
        self.tickers = {"FLAKY": lambda call: self.moving(97.0) if call > 1 else _ticker(pd.DataFrame())}

        data = self.screen([("FLAKY", "portfolio"), ("DEAD", "portfolio")])

        self.assertEqual(self.calls, {"FLAKY": 2, "DEAD": 2})
        self.assertEqual([(r["symbol"], r.get("crossed")) for r in data["results"]],
                         [("FLAKY", ["move -3.0%"]), ("DEAD", None)])
        self.assertEqual(len(self.report.issues), 1)

    def test_the_callers_tickers_have_no_list_and_their_issues_name_yahoo(self):
        self.tickers = {"XOM": self.moving(100.0)}

        data = self.screen([("XOM", None), ("ZZZZ", None)])

        self.assertNotIn("list", data["results"][0])
        self.assertTrue(self.report.issue_lines()[0].startswith("Yahoo Finance: ZZZZ: no data"))

    def test_the_thresholds_come_from_the_rules_passed(self):
        self.tickers = {"XOM": self.moving(104.6, volume=2600)}

        data = self.screen([("XOM", "watchlist")], {**DEFAULTS, "move_pct": 5.0, "volume_ratio": 3.0})

        self.assertEqual((data["results"][0]["crossed"], data["crossed_any"]), ([], 0))
        self.assertEqual(data["thresholds"]["move_pct"], 5.0)

    def test_prices_carry_iso_codes_and_a_missing_currency_is_an_issue(self):
        self.tickers = {"VOD.L": self.moving(1234.0, previous=1234.0, currency="GBp"),
                        "ODD": self.moving(10.0, previous=10.0, currency=None)}

        data = self.screen([("VOD.L", "watchlist"), ("ODD", "watchlist")])

        self.assertEqual({r["symbol"]: r["price"] for r in data["results"]},
                         {"VOD.L": "GBP 12.34", "ODD": "10.00 (currency unknown)"})
        self.assertEqual(self.report.issue_lines(), [
            "pkm/wallet/stock/watchlist.md: ODD: Yahoo Finance gave no currency, so its price has no code",
        ])

    def test_a_stale_session_says_so_and_a_lagging_ticker_shows_its_date(self):
        self.tickers = {"WMT": self.moving(100.0), "HALT": self.moving(100.0, end=_today() - timedelta(days=3))}

        data = self.screen([("WMT", "watchlist"), ("HALT", "watchlist")])

        self.assertTrue(data["as_of_today"])
        halted = next(r for r in data["results"] if r["symbol"] == "HALT")
        self.assertEqual(halted["as_of_date"], (_today() - timedelta(days=3)).isoformat())

        self.tickers = {"WMT": self.moving(100.0, end=_today() - timedelta(days=1))}
        self.assertFalse(self.screen([("WMT", "watchlist")])["as_of_today"])

    def test_too_many_tickers_is_refused_before_any_fetch(self):
        with self.assertRaisesRegex(ToolError, "at most 50 tickers per screen \\(got 51\\)"):
            self.screen([(f"T{i}", None) for i in range(51)])
        self.assertEqual(self.calls, {})

    def test_no_tickers_is_an_empty_screen_without_a_request(self):
        data = self.screen([])

        self.assertEqual((data["count"], data["crossed_any"], data["results"]), (0, 0, []))
        self.assertEqual((data["as_of_date"], data["as_of_today"]), (None, False))
        self.assertEqual(data["thresholds"], DEFAULTS)
        self.assertEqual((self.report.status, self.report.reads), ("ok", []))
        self.assertEqual(self.calls, {})


if __name__ == "__main__":
    unittest.main()
