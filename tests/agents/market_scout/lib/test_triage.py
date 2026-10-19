"""The screen's arithmetic: the thresholds and their defaults, crossings, prices, order, the scan's gate and table."""
import unittest

from agents.market_scout.lib.rules import RULES
from agents.market_scout.lib.triage import (
    any_data,
    crossed,
    latest_session,
    money,
    screen_rows,
    screen_table,
    thresholds_text,
    wake,
)

DEFAULTS = {rule.key: rule.default for rule in RULES}


def quote(symbol="PEP", move=0.5, days=None, ratio=1.0, price=100.0, earnings=None, currency="USD",
          as_of="2026-10-16"):
    return {
        "symbol": symbol,
        "currency": currency,
        "current_price": price,
        "daily_movement_pct": move,
        "volume_ratio": ratio,
        "next_earnings_date": earnings,
        "days_to_earnings": days,
        "as_of_date": as_of,
    }


def missing(symbol="NOPE", error="ValueError: No price data returned"):
    return {"symbol": symbol, "error": error}


class TestRules(unittest.TestCase):

    def test_the_defaults_are_the_values_the_screen_always_used(self):
        self.assertEqual(DEFAULTS, {"move_pct": 3.0, "earnings_days": 7, "volume_ratio": 2.0})
        self.assertEqual([type(rule.default) for rule in RULES], [float, int, float])


class TestCrossed(unittest.TestCase):

    def test_each_boundary_counts(self):
        self.assertEqual(crossed(quote(move=3.0), DEFAULTS), ["move +3.0%"])
        self.assertEqual(crossed(quote(move=-3.0), DEFAULTS), ["move -3.0%"])
        self.assertEqual(crossed(quote(days=7), DEFAULTS), ["earnings in 7 d"])
        self.assertEqual(crossed(quote(days=0), DEFAULTS), ["earnings today"])
        self.assertEqual(crossed(quote(ratio=2.0), DEFAULTS), ["volume 2.0x avg"])

    def test_just_inside_crosses_nothing(self):
        self.assertEqual(crossed(quote(move=2.99, days=8, ratio=1.99), DEFAULTS), [])
        self.assertEqual(crossed(quote(move=-2.99, days=-1), DEFAULTS), [])

    def test_missing_values_never_cross(self):
        self.assertEqual(crossed(quote(move=None, days=None, ratio=None), DEFAULTS), [])

    def test_every_crossed_threshold_is_listed_in_the_rules_order(self):
        self.assertEqual(crossed(quote(move=4.6, days=5, ratio=2.61), DEFAULTS),
                         ["move +4.6%", "earnings in 5 d", "volume 2.6x avg"])

    def test_the_values_come_from_the_rules_passed(self):
        rules = {"move_pct": 4.0, "earnings_days": 0, "volume_ratio": 1.5}
        self.assertEqual(crossed(quote(move=3.5, days=1, ratio=1.6), rules), ["volume 1.6x avg"])
        self.assertEqual(crossed(quote(move=-4.0, days=0, ratio=1.0), rules), ["move -4.0%", "earnings today"])


class TestMoney(unittest.TestCase):

    def test_an_iso_code_before_the_amount(self):
        self.assertEqual(money(182.4, "USD"), "USD 182.40")
        self.assertEqual(money(1336.64, "USD"), "USD 1,336.64")
        self.assertEqual(money(2345.0, "JPY"), "JPY 2,345.00")
        self.assertEqual(money(10.5, "usd"), "USD 10.50")

    def test_minor_units_become_main_units(self):
        self.assertEqual(money(1234.0, "GBp"), "GBP 12.34")
        self.assertEqual(money(1234.0, "GBX"), "GBP 12.34")
        self.assertEqual(money(5000.0, "ZAc"), "ZAR 50.00")
        self.assertEqual(money(2500.0, "ILA"), "ILS 25.00")

    def test_no_currency_is_marked_and_no_price_is_none(self):
        self.assertEqual(money(182.4, None), "182.40 (currency unknown)")
        self.assertEqual(money(182.4, ""), "182.40 (currency unknown)")
        self.assertIsNone(money(None, "USD"))


class TestScreenRows(unittest.TestCase):

    def test_crossed_then_unavailable_then_the_rest_biggest_move_first(self):
        rows = screen_rows([
            quote("PEP", move=-1.11),
            missing("NOPE"),
            quote("XOM", move=4.6, ratio=2.6),
            quote("IBM", move=2.5),
            quote("CAT", move=-6.0),
            quote("JNJ", move=0.2, days=3),
        ], DEFAULTS)
        self.assertEqual([r["symbol"] for r in rows], ["CAT", "XOM", "JNJ", "NOPE", "IBM", "PEP"])
        self.assertEqual(rows[1]["crossed"], ["move +4.6%", "volume 2.6x avg"])
        self.assertEqual(rows[3], {"symbol": "NOPE", "error": "ValueError: No price data returned"})
        self.assertEqual(rows[4]["crossed"], [])

    def test_unavailable_rows_keep_their_input_order(self):
        rows = screen_rows([missing("B"), missing("A")], DEFAULTS)
        self.assertEqual([r["symbol"] for r in rows], ["B", "A"])

    def test_a_result_holds_facts_with_its_list_and_an_iso_price(self):
        [row] = screen_rows([quote("XOM", move=4.6, ratio=2.6, price=112.3, days=6, earnings="2026-10-22")],
                            DEFAULTS, {"XOM": "watchlist"})
        self.assertEqual(row, {
            "symbol": "XOM", "list": "watchlist", "crossed": ["move +4.6%", "earnings in 6 d", "volume 2.6x avg"],
            "price": "USD 112.30", "daily_movement_pct": 4.6, "volume_ratio": 2.6,
            "next_earnings_date": "2026-10-22", "days_to_earnings": 6,
        })
        self.assertNotIn("status", row)

    def test_a_lagging_ticker_shows_its_own_session(self):
        rows = screen_rows([quote("WMT"), quote("HALT", as_of="2026-10-13")], DEFAULTS)
        self.assertEqual({r["symbol"]: r.get("as_of_date") for r in rows}, {"WMT": None, "HALT": "2026-10-13"})
        self.assertEqual(latest_session([quote(as_of="2026-10-13"), missing(), quote(as_of="2026-10-16")]), "2026-10-16")
        self.assertIsNone(latest_session([missing()]))


class TestWake(unittest.TestCase):

    def rows(self, *quotes):
        return screen_rows(quotes, DEFAULTS)

    def test_a_crossing_in_todays_session_wakes_the_agent(self):
        rows = self.rows(quote("XOM", move=4.6, ratio=2.6), missing("NOPE"), quote("PEP", move=-1.11))
        self.assertEqual(wake(rows, "2026-10-16", "2026-10-16"),
                         (True, "1 of 3 crossed a threshold: XOM (move +4.6%, volume 2.6x avg)"))

    def test_a_quiet_day_does_not(self):
        self.assertEqual(wake(self.rows(quote("PEP"), missing("NOPE")), "2026-10-16", "2026-10-16"),
                         (False, "quiet: none of 2 tickers crossed a threshold"))

    def test_a_day_without_a_session_does_not_even_with_old_moves(self):
        self.assertEqual(wake(self.rows(quote("CAT", move=-6.0)), "2026-10-13", "2026-10-16"),
                         (False, "no session today (latest data is from 2026-10-13)"))

    def test_no_data_at_all_does_not(self):
        rows = self.rows(missing("PEP"), missing("XOM"))
        self.assertFalse(any_data(rows))
        self.assertEqual(wake(rows, None, "2026-10-16"), (False, "market data unavailable for all 2 tickers"))


class TestTexts(unittest.TestCase):

    def test_the_thresholds_in_words(self):
        self.assertEqual(thresholds_text(DEFAULTS), "move ≥ 3%, earnings within 7 days, volume ≥ 2x avg")
        self.assertEqual(thresholds_text({"move_pct": 2.5, "earnings_days": 3, "volume_ratio": 1.75}),
                         "move ≥ 2.5%, earnings within 3 days, volume ≥ 1.75x avg")

    def test_the_screen_table(self):
        rows = screen_rows([
            quote("XOM", move=4.6, ratio=2.6, price=112.3, days=6, earnings="2026-10-22"),
            quote("PEP", move=-1.11, ratio=1.02, price=1336.64),
            missing("NOPE"),
            quote("ODD", move=0.1, price=10.0, currency=None),
        ], DEFAULTS)
        lines = screen_table(rows).splitlines()
        self.assertEqual(lines[0], "| Symbol | Price | Δ% | Vol × Avg | Next Earnings | Crossed |")
        self.assertEqual(
            lines[2], "| XOM | USD 112.30 | +4.60% | 2.6x | 2026-10-22 (6d) | move +4.6%, earnings in 6 d, volume 2.6x avg |"
        )
        self.assertEqual(lines[3], "| NOPE | — | — | — | — | data unavailable |")
        self.assertEqual(lines[4], "| PEP | USD 1,336.64 | -1.11% | 1.0x | — | — |")
        self.assertEqual(lines[5], "| ODD | 10.00 (currency unknown) | +0.10% | 1.0x | — | — |")


if __name__ == "__main__":
    unittest.main()
