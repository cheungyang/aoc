"""The screen's arithmetic: which thresholds a quote crossed, the order, prices, the scan's gate and table.

A quote crosses a threshold (values from RULES.md; each boundary counts):

    price move     |daily_movement_pct| >= move_pct          "move +4.6%"
    earnings soon  0 <= days_to_earnings <= earnings_days    "earnings in 5 d"
    volume spike   volume_ratio >= volume_ratio (the rule)   "volume 2.6x avg"

These are facts. What a crossing means, and what to research, is the agent's
call (its market_data skill). WORKFLOW.md states each rule with worked
examples; everything here is pure, so those examples run as tests.
"""
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# Yahoo quotes some exchanges in minor units: code -> (ISO code, minor units per unit).
MINOR_UNITS = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100), "ILA": ("ILS", 100)}

# What a result carries besides symbol, list and crossed.
RESULT_FIELDS = ("daily_movement_pct", "volume_ratio", "next_earnings_date", "days_to_earnings")

TABLE_HEADER = (
    "| Symbol | Price | Δ% | Vol × Avg | Next Earnings | Crossed |\n"
    "| :--- | ---: | ---: | ---: | :--- | :--- |"
)


def crossed(quote: Mapping[str, Any], rules: Mapping[str, Any]) -> List[str]:
    """The label of every threshold `quote` crosses, in RULES order: move, earnings, volume.

    A missing number never crosses.
    """
    labels = []
    move = quote.get("daily_movement_pct")
    if move is not None and abs(move) >= rules["move_pct"]:
        labels.append(f"move {move:+.1f}%")
    days = quote.get("days_to_earnings")
    if days is not None and 0 <= days <= rules["earnings_days"]:
        labels.append("earnings today" if days == 0 else f"earnings in {days} d")
    ratio = quote.get("volume_ratio")
    if ratio is not None and ratio >= rules["volume_ratio"]:
        labels.append(f"volume {ratio:.1f}x avg")
    return labels


def money(value: Optional[float], currency: Optional[str]) -> Optional[str]:
    """A price with its ISO currency code, "USD 1,336.64". Minor units (pence, cents) become main units.

    Without a currency the amount is marked, never guessed: "182.40 (currency unknown)".
    """
    if value is None:
        return None
    code, per_unit = MINOR_UNITS.get(currency or "", ((currency or "").strip().upper(), 1))
    amount = f"{value / per_unit:,.2f}"
    return f"{code} {amount}" if code else f"{amount} (currency unknown)"


def latest_session(quotes: Iterable[Mapping[str, Any]]) -> Optional[str]:
    """The latest session date among the quotes (YYYY-MM-DD); None when none has data."""
    sessions = [quote["as_of_date"] for quote in quotes if quote.get("as_of_date")]
    return max(sessions) if sessions else None


def screen_rows(quotes: Iterable[Mapping[str, Any]], rules: Mapping[str, Any],
                lists: Optional[Mapping[str, str]] = None) -> List[Dict[str, Any]]:
    """One result per quote, in the screen's order.

    Tickers that crossed a threshold come first, then those without data (in
    input order), then the rest; in the first and last group, the biggest move
    either way first. A quote with an `error` keeps only its symbol, list and
    error. `lists` maps a symbol to "portfolio" or "watchlist". A result carries
    its own `as_of_date` only when it lags the latest session.
    """
    quotes = list(quotes)
    latest = latest_session(quotes)
    rows = [_result(quote, rules, (lists or {}).get(quote["symbol"]), latest) for quote in quotes]
    return sorted(rows, key=_order)


def _result(quote: Mapping[str, Any], rules: Mapping[str, Any], list_name: Optional[str],
            latest: Optional[str]) -> Dict[str, Any]:
    row: Dict[str, Any] = {"symbol": quote["symbol"]}
    if list_name:
        row["list"] = list_name
    if quote.get("error"):
        row["error"] = quote["error"]
        return row
    row["crossed"] = crossed(quote, rules)
    row["price"] = money(quote.get("current_price"), quote.get("currency"))
    row.update({field: quote.get(field) for field in RESULT_FIELDS})
    if quote.get("as_of_date") and quote["as_of_date"] != latest:
        row["as_of_date"] = quote["as_of_date"]
    return row


def _order(row: Mapping[str, Any]):
    if "error" in row:
        return 1, 0
    move = row.get("daily_movement_pct")
    return (0 if row["crossed"] else 2), (-abs(move) if move is not None else 0)


def any_data(rows: Iterable[Mapping[str, Any]]) -> bool:
    """Whether at least one ticker has data."""
    return any("error" not in row for row in rows)


def wake(rows: Sequence[Mapping[str, Any]], as_of_date: Optional[str], today: str) -> Tuple[bool, str]:
    """Whether the daily scan wakes the agent, and the reason it logs.

    Only when a ticker crossed a threshold in today's session. No data at all, a
    day without a session (the latest data is older than `today`) and a day
    without a crossing are a no.
    """
    if not any_data(rows):
        return False, f"market data unavailable for all {len(rows)} tickers"
    if as_of_date != today:
        return False, f"no session today (latest data is from {as_of_date})"
    hits = [row for row in rows if row.get("crossed")]
    if not hits:
        return False, f"quiet: none of {len(rows)} tickers crossed a threshold"
    reasons = "; ".join(f"{row['symbol']} ({', '.join(row['crossed'])})" for row in hits)
    return True, f"{len(hits)} of {len(rows)} crossed a threshold: {reasons}"


def thresholds_text(rules: Mapping[str, Any]) -> str:
    """The thresholds in words: "move ≥ 3%, earnings within 7 days, volume ≥ 2x avg"."""
    return (f"move ≥ {rules['move_pct']:g}%, earnings within {rules['earnings_days']} days, "
            f"volume ≥ {rules['volume_ratio']:g}x avg")


def screen_table(rows: Iterable[Mapping[str, Any]]) -> str:
    """The screen as a markdown table, in the rows' order: the scan's findings."""
    lines = [TABLE_HEADER]
    for row in rows:
        if "error" in row:
            lines.append(f"| {row['symbol']} | — | — | — | — | data unavailable |")
            continue
        lines.append(
            f"| {row['symbol']} | {row.get('price') or '—'} | {_signed(row.get('daily_movement_pct'))} "
            f"| {_ratio(row.get('volume_ratio'))} | {_earnings(row)} | {', '.join(row['crossed']) or '—'} |"
        )
    return "\n".join(lines)


def _signed(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:+.2f}%"


def _ratio(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.1f}x"


def _earnings(row: Mapping[str, Any]) -> str:
    when, days = row.get("next_earnings_date"), row.get("days_to_earnings")
    if not when:
        return "—"
    return f"{when} ({days}d)" if days is not None else when
