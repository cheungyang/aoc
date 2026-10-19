"""The market_screen tool: one call, the envelope, thresholds from RULES.md. Yahoo is mocked."""
import json
import unittest

import pandas as pd

from tests.helpers import envelope, execution_context
from tests.tools.test_yfinance import _ticker
from tests.agents.market_scout.lib.test_quotes import MockedYahoo
from agents.market_scout.tools.market_screen import market_screen


class TestMarketScreenTool(MockedYahoo):

    def call(self, args=None, agent_id="market-scout"):
        with execution_context(agent_id=agent_id):
            return envelope(market_screen.invoke(args or {}))

    def test_every_ticker_read_is_ok(self):
        self.write("portfolio.md", "# Stock Portfolio\n\n- PEP\n")
        self.write("watchlist.md", "# Watchlist\n\n- IBM\n")
        self.tickers = {"PEP": self.moving(96.0), "IBM": self.moving(100.5)}

        out = self.call()

        self.assertEqual((out["status"], out["issues"], out["errors"]), ("ok", "None", "None"))
        data = json.loads(out["payload"])
        self.assertEqual([(r["symbol"], r["list"], r["crossed"], r["price"]) for r in data["results"]],
                         [("PEP", "portfolio", ["move -4.0%"], "USD 96.00"), ("IBM", "watchlist", [], "USD 100.50")])
        self.assertEqual((data["count"], data["crossed_any"]), (2, 1))
        self.assertNotIn("explain", out)

    def test_a_ticker_without_data_makes_it_partial(self):
        self.write("portfolio.md", "- PEP\n- NOPE\n")
        self.write("watchlist.md", "")
        self.tickers = {"PEP": self.moving(96.0)}

        out = self.call()

        self.assertEqual((out["status"], out["errors"]), ("partial", "None"))
        self.assertEqual(out["issues"], "- pkm/wallet/stock/portfolio.md: NOPE: no data from Yahoo Finance after a retry "
                                        "(ValueError: No price data returned (invalid or delisted symbol?))")
        data = json.loads(out["payload"])
        self.assertEqual([r["symbol"] for r in data["results"]], ["PEP", "NOPE"])
        self.assertIn("error", data["results"][1])

    def test_list_lines_it_cant_read_come_back_raw(self):
        self.write("portfolio.md", "- PEP\n")
        self.write("watchlist.md", "# Watchlist\n\n- xom\nCAT, maybe\n")
        self.tickers = {"PEP": self.moving(100.0)}

        out = self.call()

        self.assertEqual(out["status"], "partial")
        self.assertEqual(out["issues"], "- pkm/wallet/stock/watchlist.md: 2 lines not recognized\n    - xom\n    CAT, maybe")
        self.assertEqual([r["symbol"] for r in json.loads(out["payload"])["results"]], ["PEP"])

    def test_missing_or_empty_lists_are_an_empty_screen(self):
        for lists in ({}, {"portfolio.md": "# Stock Portfolio\n", "watchlist.md": ""}):
            with self.subTest(lists=sorted(lists)):
                for name, text in lists.items():
                    self.write(name, text)

                out = self.call()

                self.assertEqual((out["status"], out["issues"], out["errors"]), ("ok", "None", "None"))
                data = json.loads(out["payload"])
                self.assertEqual((data["count"], data["crossed_any"], data["results"]), (0, 0, []))
                self.assertEqual(self.calls, {})

    def test_no_data_for_any_ticker_fails(self):
        self.write("portfolio.md", "- PEP\n- IBM\n")
        self.write("watchlist.md", "")

        out = self.call()

        self.assertEqual((out["status"], out["payload"]), ("failed", ""))
        self.assertIn("- pkm/wallet/stock/portfolio.md: PEP: no data from Yahoo Finance after a retry", out["issues"])
        self.assertIn("- Yahoo Finance: no data for any of the 2 tickers, after a retry (required)", out["issues"])

    def test_explicit_tickers_skip_the_lists(self):
        self.write("portfolio.md", "- PEP\n")
        self.tickers = {"XOM": self.moving(100.0)}

        out = self.call({"tickers": ["xom"]})

        self.assertEqual(out["status"], "ok")
        [row] = json.loads(out["payload"])["results"]
        self.assertEqual(row["symbol"], "XOM")
        self.assertNotIn("list", row)
        self.assertNotIn("PEP", self.calls)

    def test_a_bad_tickers_argument_is_an_error(self):
        out = self.call({"tickers": [" "]})
        self.assertEqual(out["status"], "failed")
        self.assertIn("Error: 'tickers' names no symbols", out["errors"])
        out = self.call({"tickers": [f"T{i}" for i in range(51)]})
        self.assertEqual(out["errors"], "Error: at most 50 tickers per screen (got 51)")
        self.assertEqual(self.calls, {})

    def test_rules_md_sets_the_thresholds_and_explain_shows_the_inputs(self):
        self.write("portfolio.md", "- PEP\n")
        self.write("watchlist.md", "")
        self.write_rules("# market-scout: rules\n\n- move_pct: 5  # my alert line\n")
        self.tickers = {"PEP": self.moving(96.0)}

        out = self.call({"explain": True})

        self.assertEqual(out["status"], "ok")
        data = json.loads(out["payload"])
        self.assertEqual(data["thresholds"], {"move_pct": 5.0, "earnings_days": 7, "volume_ratio": 2.0})
        self.assertEqual((data["results"][0]["crossed"], data["crossed_any"]), ([], 0))
        explain = out["explain"]
        self.assertRegex(explain, r"- pkm/agents/market-scout/RULES\.md \(modified [\d-]+ [\d:]+, 1 record\)")
        self.assertIn("- pkm/wallet/stock/portfolio.md (modified", explain)
        self.assertIn("- Yahoo Finance (1 record)", explain)
        self.assertIn("- PEP: close 96.0 USD on", explain)
        self.assertIn("crossed: none", explain)

    def test_a_rules_value_that_doesnt_read_is_an_issue_and_the_default_applies(self):
        self.write("portfolio.md", "- PEP\n")
        self.write("watchlist.md", "")
        self.write_rules("- move_pct: lots\n")
        self.tickers = {"PEP": self.moving(96.0)}

        out = self.call()

        self.assertEqual(out["status"], "partial")
        self.assertEqual(out["issues"], "- pkm/agents/market-scout/RULES.md: line 1: 'move_pct: lots' can't be read "
                                        "(expected a number, got 'lots'); using the default 3.0")
        self.assertEqual(json.loads(out["payload"])["results"][0]["crossed"], ["move -4.0%"])

    def test_an_agent_without_the_grant_is_refused(self):
        self.tickers = {"PEP": _ticker(pd.DataFrame())}
        out = self.call({"tickers": ["PEP"]}, agent_id="property-scout")
        self.assertEqual(out["status"], "failed")
        self.assertIn("does not have permission to use market_screen", out["errors"])
        self.assertEqual(self.calls, {})

    def test_outside_an_agent_run_it_is_refused(self):
        out = envelope(market_screen.invoke({"tickers": ["PEP"]}))
        self.assertIn("no active execution context", out["errors"])
        self.assertEqual(self.calls, {})


if __name__ == "__main__":
    unittest.main()
