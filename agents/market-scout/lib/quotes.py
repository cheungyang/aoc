"""The market_screen run: Yahoo quotes for the tickers, checked against the thresholds.

Reuses the shared yfinance tool's fetch functions (the same Yahoo quotes its
'quote' action returns); the market_screen tool and scan both call
`screen_quotes`. The arithmetic is in triage.py.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from core.tooling import Report, ToolError, get_local_now
from tools.yfinance import MAX_TICKERS, _fetch, _quote
from agents.market_scout.lib import triage
from agents.market_scout.lib.tickers import list_path

YAHOO = "Yahoo Finance"


def screen_quotes(followed: Sequence[Tuple[str, Optional[str]]], rules: Mapping[str, Any],
                  report: Report) -> Dict[str, Any]:
    """The screen payload for (symbol, list) pairs; the list is None for a ticker the caller named.

    Quotes that fail are retried once, together. A ticker still without data,
    or a price without a currency, goes in `report` (partial), named after its
    list file. No tickers is an empty screen, with no request to Yahoo. Raises
    ToolError for more than MAX_TICKERS.
    """
    if len(followed) > MAX_TICKERS:
        raise ToolError(f"at most {MAX_TICKERS} tickers per screen (got {len(followed)})")
    symbols = [symbol for symbol, _ in followed]
    lists = {symbol: name for symbol, name in followed if name}

    quotes: List[Dict[str, Any]] = []
    if symbols:
        quotes = _fetch(_quote, symbols)
        failed = [i for i, quote in enumerate(quotes) if "error" in quote]
        if failed:
            for i, retried in zip(failed, _fetch(_quote, [symbols[i] for i in failed])):
                quotes[i] = retried
        _account(quotes, lists, rules, report)

    rows = triage.screen_rows(quotes, rules, lists)
    as_of_date = triage.latest_session(quotes)
    return {
        "thresholds": dict(rules),
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of_date": as_of_date,
        "as_of_today": as_of_date == get_local_now().date().isoformat(),
        "count": len(rows),
        "crossed_any": sum(bool(row.get("crossed")) for row in rows),
        "results": rows,
    }


def no_data(count: int) -> str:
    """The issue when none of `count` tickers has data, even after the retry."""
    return f"no data for any of the {count} tickers, after a retry"


def _account(quotes: List[Dict[str, Any]], lists: Mapping[str, str], rules: Mapping[str, Any],
             report: Report) -> None:
    """What each quote gave: an issue for what's missing, and the inputs behind each number."""
    for quote in quotes:
        symbol = quote["symbol"]
        source = list_path(lists[symbol]) if symbol in lists else YAHOO
        if quote.get("error"):
            report.problem(source, f"{symbol}: no data from {YAHOO} after a retry ({quote['error']})")
            continue
        if not quote.get("currency"):
            report.problem(source, f"{symbol}: {YAHOO} gave no currency, so its price has no code")
        report.trace(_trace(quote, triage.crossed(quote, rules)))
    report.read(YAHOO, records=sum(not quote.get("error") for quote in quotes))


def _trace(quote: Mapping[str, Any], labels: List[str]) -> str:
    earnings = (f"next earnings {quote['next_earnings_date']} -> {quote.get('days_to_earnings')} d"
                if quote.get("next_earnings_date") else "no earnings date")
    return (
        f"{quote['symbol']}: close {quote.get('current_price')} {quote.get('currency')} on {quote.get('as_of_date')}, "
        f"previous close {quote.get('previous_close')} -> move {quote.get('daily_movement_pct')}%; "
        f"volume {quote.get('volume')} over a 20-session average of {quote.get('avg_volume')} "
        f"-> {quote.get('volume_ratio')}x; {earnings}; crossed: {', '.join(labels) or 'none'}"
    )
