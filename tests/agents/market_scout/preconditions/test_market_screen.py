"""market_screen wakes market-scout only when a ticker crossed a threshold today. Yahoo is mocked."""
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from core.scheduler.preconditions import PreconditionError, build_precondition
from core.scheduler.spec import Findings, Problems, ScheduleContext
from core.util.config import Config
from core.util.time_util import get_local_timezone
from agents.market_scout.preconditions.market_screen import MarketScreen

AGENT = "market-scout"


def build(**config):
    return build_precondition({"type": "market_screen", **config}, agent_id=AGENT)


def ctx():
    return ScheduleContext(schedule_id="market-scout:daily-market-scan", agent_id=AGENT, now=time.time())


def today(days=0):
    return (datetime.now(get_local_timezone()).date() + timedelta(days=days)).isoformat()


def row(symbol, crossed=(), move=0.5, price="USD 100.00", ratio=1.0, error=None):
    if error:
        return {"symbol": symbol, "error": error}
    return {"symbol": symbol, "crossed": list(crossed), "price": price, "daily_movement_pct": move,
            "volume_ratio": ratio, "next_earnings_date": None, "days_to_earnings": None}


def screen(rows, as_of_date=None):
    return {"thresholds": {}, "as_of_date": as_of_date or today(), "results": rows, "count": len(rows),
            "crossed_any": sum(bool(r.get("crossed")) for r in rows)}


class TempLists(unittest.TestCase):
    """A temp PKM holding the two lists, and RULES.md when a test writes one."""

    def setUp(self):
        self.pkm = tempfile.mkdtemp()
        Config().pkm_dir = self.pkm
        self.addCleanup(setattr, Config(), "pkm_dir", None)
        self.addCleanup(shutil.rmtree, self.pkm, True)
        self.write("wallet/stock/portfolio.md", "# Stock Portfolio\n\n- PEP\n")
        self.write("wallet/stock/watchlist.md", "# Watchlist\n\n- XOM\n")

    def write(self, relative, text):
        path = os.path.join(self.pkm, *relative.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)


class TestMarketScreen(TempLists):
    """The decision, with the screen itself stubbed."""

    def evaluate(self, payload):
        with patch("agents.market_scout.lib.quotes.screen_quotes", return_value=payload) as run:
            decision = build().evaluate(ctx())
        run.assert_called_once()
        followed, rules, _ = run.call_args.args
        self.assertEqual(followed, [("PEP", "portfolio"), ("XOM", "watchlist")])
        self.assertEqual(rules, {"move_pct": 3.0, "earnings_days": 7, "volume_ratio": 2.0})
        return decision

    def test_is_declared_by_its_type_for_market_scout(self):
        self.assertIsInstance(build(), MarketScreen)
        with self.assertRaises(PreconditionError):
            build_precondition({"type": "market_screen"}, agent_id="property-scout")

    def test_takes_no_args(self):
        with self.assertRaises(PreconditionError):
            build(args={"move_pct": 2})

    def test_a_crossing_runs_with_the_screen_table(self):
        decision = self.evaluate(screen([
            row("XOM", ["move +4.6%", "volume 2.6x avg"], move=4.6, price="USD 112.30", ratio=2.6),
            row("NOPE", error="ValueError: No price data returned"),
            row("PEP", move=-1.11, price="USD 152.18"),
        ]))
        self.assertTrue(decision)
        self.assertEqual(decision.reason, "1 of 3 crossed a threshold: XOM (move +4.6%, volume 2.6x avg)")
        self.assertIsInstance(decision.detail, Findings)
        self.assertEqual((decision.detail.source, decision.detail.issues), ("market_screen", ()))
        text = decision.detail.text
        self.assertTrue(text.startswith(f"1 of 3 tickers crossed a threshold (move ≥ 3%, earnings within 7 days, "
                                        f"volume ≥ 2x avg) in the {today()} session."))
        self.assertIn("| XOM | USD 112.30 | +4.60% | 2.6x | — | move +4.6%, volume 2.6x avg |", text)
        self.assertIn("| NOPE | — | — | — | — | data unavailable |", text)
        self.assertIn("| PEP | USD 152.18 | -1.11% | 1.0x | — | — |", text)

    def test_a_quiet_day_skips(self):
        decision = self.evaluate(screen([row("PEP"), row("XOM")]))
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "quiet: none of 2 tickers crossed a threshold")
        self.assertIsNone(decision.detail)

    def test_a_day_without_a_session_skips_even_with_old_moves(self):
        decision = self.evaluate(screen([row("CAT", ["move -6.0%"], move=-6.0)], as_of_date=today(-3)))
        self.assertFalse(decision)
        self.assertEqual(decision.reason, f"no session today (latest data is from {today(-3)})")

    def test_no_data_at_all_is_posted(self):
        decision = self.evaluate(screen([row("PEP", error="x"), row("XOM", error="y")]))
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "market data unavailable for all 2 tickers (with issues)")
        self.assertEqual(decision.detail, Problems(
            "market_screen", ("Yahoo Finance: no data for any of the 2 tickers, after a retry",)))

    def test_an_error_fails_closed_and_is_posted(self):
        with patch("agents.market_scout.lib.quotes.screen_quotes", side_effect=ValueError("boom")):
            decision = build().evaluate(ctx())
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "market_screen can't run: the check failed (ValueError: boom)")
        self.assertIsInstance(decision.detail, Problems)

    def test_a_network_error_during_the_fetch_is_only_logged(self):
        with patch("agents.market_scout.lib.quotes.screen_quotes", side_effect=ConnectionError("Yahoo unreachable")):
            decision = build().evaluate(ctx())
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "market_screen failed (ConnectionError: Yahoo unreachable)")
        self.assertIsNone(decision.detail)

    def test_missing_or_empty_lists_skip_quietly(self):
        def quiet_skip():
            with patch("agents.market_scout.lib.quotes.screen_quotes") as run:
                decision = build().evaluate(ctx())
            run.assert_not_called()
            self.assertFalse(decision)
            self.assertEqual(decision.reason, "nothing to screen: portfolio.md and watchlist.md list no tickers")
            self.assertIsNone(decision.detail)

        for name in ("portfolio.md", "watchlist.md"):
            os.remove(os.path.join(self.pkm, "wallet", "stock", name))
        quiet_skip()
        self.write("wallet/stock/portfolio.md", "# Stock Portfolio\n")
        self.write("wallet/stock/watchlist.md", "")
        quiet_skip()

    def test_a_list_line_that_isnt_a_ticker_is_posted_even_with_no_tickers(self):
        self.write("wallet/stock/portfolio.md", "# Stock Portfolio\n")
        self.write("wallet/stock/watchlist.md", "- xom\n")
        decision = build().evaluate(ctx())
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "nothing to screen: portfolio.md and watchlist.md list no tickers (with issues)")
        self.assertEqual(decision.detail, Problems("market_screen", ("pkm/wallet/stock/watchlist.md: 1 line not recognized",)))


class TestMarketScreenEndToEnd(TempLists):
    """The real screen over temp lists, with yf.Ticker mocked."""

    def run_scan(self, xom=None, pep=99.0):
        """Each ticker's last close after 21 closes of 100.0; None, or a ticker not named, has no data."""
        import pandas as pd
        from tests.tools.test_yfinance import _hist, _ticker

        closes = {"XOM": xom, "PEP": pep}
        quotes = {symbol: _ticker(_hist([100.0] * 21 + [close], [1000] * 22))
                  for symbol, close in closes.items() if close is not None}
        with patch("tools.yfinance.yf.Ticker", side_effect=lambda s: quotes.get(s, _ticker(pd.DataFrame()))):
            return build().evaluate(ctx())

    def test_fires_on_a_mover(self):
        decision = self.run_scan(xom=96.5)
        self.assertTrue(decision)
        self.assertEqual(decision.reason, "1 of 2 crossed a threshold: XOM (move -3.5%)")
        self.assertIn("| XOM | USD 96.50 | -3.50% |", decision.detail.text)
        self.assertEqual(decision.detail.issues, ())

    def test_skips_when_quiet(self):
        decision = self.run_scan(xom=102.9)
        self.assertFalse(decision)
        self.assertIsNone(decision.detail)

    def test_rules_md_moves_the_gate(self):
        self.write("agents/market-scout/RULES.md", "- move_pct: 4\n")
        decision = self.run_scan(xom=96.5)
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "quiet: none of 2 tickers crossed a threshold")

    def test_a_ticker_without_data_rides_along_on_a_day_with_work(self):
        self.write("wallet/stock/watchlist.md", "# Watchlist\n\n- XOM\n- NOPE\n")
        decision = self.run_scan(xom=96.5)
        self.assertTrue(decision)
        [issue] = decision.detail.issues
        self.assertTrue(issue.startswith("pkm/wallet/stock/watchlist.md: NOPE: no data from Yahoo Finance after a retry"))
        self.assertIn("| NOPE | — | — | — | — | data unavailable |", decision.detail.text)

    def test_what_it_cant_read_on_a_quiet_day_is_posted(self):
        self.write("wallet/stock/watchlist.md", "# Watchlist\n\n- XOM\n- NOPE\n- xom\n")
        decision = self.run_scan(xom=102.9)
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "quiet: none of 3 tickers crossed a threshold (with issues)")
        self.assertIsInstance(decision.detail, Problems)
        self.assertEqual(decision.detail.issues[0], "pkm/wallet/stock/watchlist.md: 1 line not recognized")
        self.assertTrue(decision.detail.issues[1].startswith("pkm/wallet/stock/watchlist.md: NOPE: no data"))

    def test_no_data_for_any_ticker_is_posted(self):
        decision = self.run_scan(xom=None, pep=None)
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "market data unavailable for all 2 tickers (with issues)")
        self.assertEqual(decision.detail, Problems(
            "market_screen", ("Yahoo Finance: no data for any of the 2 tickers, after a retry",)))


if __name__ == "__main__":
    unittest.main()
