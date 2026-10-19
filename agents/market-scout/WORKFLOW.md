# market-scout: how its tools work

## market_screen

One call screens the tickers in the two lists, or the `tickers` passed instead (at most 50, a limit of the shared `yfinance` tool). It returns facts: each ticker's numbers and the thresholds it crossed. The verdict (Triggered, Quiet or Unavailable) and what to research are the agent's, in its market_data skill.

Status: `ok` when every list line and every quote was read, and for an empty screen; `partial` when a list line wasn't recognized, a ticker has no data, or a price has no currency; `failed` when no ticker has data.

### Which tickers

`pkm/wallet/stock/portfolio.md`, then `watchlist.md`, one ticker per bullet. A bullet counts when its first word is a ticker as written, in capitals: `BRK.B` and `^GSPC` count, and bold, `$` or `[[link]]` around it is fine. Headings, blank lines, empty bullets and a frontmatter block are skipped.

```python
>>> from agents.market_scout.lib.tickers import parse_list
>>> parse_list("# Watchlist\n\n- XOM\n- **BRK.B** core holding\n- $IBM\n- [[CAT]]\n")
(['XOM', 'BRK.B', 'IBM', 'CAT'], [])
```

Any other line comes back raw in the issues (partial), so a ticker written another way is reported, never dropped silently. A note and a lowercase ticker aren't read as tickers, and a repeated ticker counts once:

```python
>>> parse_list("- XOM\nConsider energy names\n- xom\n- XOM\n")
(['XOM'], ['Consider energy names', '- xom'])
```

A ticker on both lists counts once, as portfolio. Either list may be missing or empty, both even, and that is no issue: with no tickers at all, the screen is empty (`count` 0) and ok. An empty list reads as no tickers and nothing unread:

```python
>>> parse_list("# Watchlist\n\n")
([], [])
```

### The quotes

Quotes come from Yahoo Finance, through the shared `yfinance` tool's quote: the latest session's close, the move from the previous close, and the session's volume over the average of the 20 sessions before it. The quotes that fail are retried once, together. A ticker still without data keeps its `error` in the results and is named in the issues, under the list it came from, or under Yahoo Finance when it was passed in (partial). When no ticker has data at all, there is no screen (failed).

Each price carries its ISO currency code. Yahoo quotes London, Johannesburg and Tel Aviv listings in minor units (pence, cents, agorot), which become main units:

```python
>>> from agents.market_scout.lib.triage import money
>>> money(182.4, "USD")
'USD 182.40'
>>> money(1336.64, "USD")
'USD 1,336.64'
>>> money(1234.0, "GBp")
'GBP 12.34'
```

A price without a currency is marked, never guessed, and named in the issues:

```python
>>> money(182.4, None)
'182.40 (currency unknown)'
```

### The thresholds

The thresholds come from RULES.md; a key it doesn't set keeps its default:

```python
>>> from agents.market_scout.lib.rules import RULES
>>> rules = {rule.key: rule.default for rule in RULES}
>>> rules
{'move_pct': 3.0, 'earnings_days': 7, 'volume_ratio': 2.0}
```

A ticker crosses a threshold when (each boundary counts):

- its daily move, up or down, is at least `move_pct` percent (default 3.0): "move +4.6%";
- its next earnings date is today or at most `earnings_days` days away (default 7): "earnings today", "earnings in 5 d";
- its volume is at least `volume_ratio` times its 20-session average (default 2.0): "volume 2.6x avg".

`crossed` lists every threshold the ticker crossed, in that order. A missing number never crosses.

```python
>>> from agents.market_scout.lib.triage import crossed
>>> crossed({"daily_movement_pct": 4.6, "days_to_earnings": 5, "volume_ratio": 2.61}, rules)
['move +4.6%', 'earnings in 5 d', 'volume 2.6x avg']
>>> crossed({"daily_movement_pct": -3.0, "days_to_earnings": 0, "volume_ratio": 2.0}, rules)
['move -3.0%', 'earnings today', 'volume 2.0x avg']
>>> crossed({"daily_movement_pct": 2.99, "days_to_earnings": 8, "volume_ratio": 1.99}, rules)
[]
>>> crossed({"daily_movement_pct": None, "days_to_earnings": None, "volume_ratio": None}, rules)
[]
```

With `- move_pct: 4` in RULES.md, a 3.5% move no longer crosses:

```python
>>> crossed({"daily_movement_pct": 3.5, "days_to_earnings": None, "volume_ratio": 1.0}, {**rules, "move_pct": 4.0})
[]
```

### The order

Tickers that crossed a threshold come first, then those without data (in list order), then the rest. In the first and the last group, the biggest move, up or down, comes first.

```python
>>> from agents.market_scout.lib.triage import screen_rows
>>> def quote(symbol, move, ratio=1.0, days=None):
...     return {"symbol": symbol, "currency": "USD", "current_price": 100.0, "daily_movement_pct": move,
...             "volume_ratio": ratio, "days_to_earnings": days, "as_of_date": "2026-10-16"}
>>> rows = screen_rows([quote("PEP", -1.11), {"symbol": "NOPE", "error": "ValueError: No price data returned"},
...                     quote("XOM", 4.6, ratio=2.6), quote("IBM", 2.5), quote("CAT", -6.0),
...                     quote("JNJ", 0.2, days=3)], rules)
>>> [(row["symbol"], row.get("crossed", "no data")) for row in rows]
[('CAT', ['move -6.0%']), ('XOM', ['move +4.6%', 'volume 2.6x avg']), ('JNJ', ['earnings in 3 d']), ('NOPE', 'no data'), ('IBM', []), ('PEP', [])]
```

## The daily scan

The `market_screen` scan runs the same screen over the two lists before the scheduled run, and wakes the agent only when a ticker crossed a threshold in today's session:

```python
>>> from agents.market_scout.lib.triage import wake
>>> wake(rows, as_of_date="2026-10-16", today="2026-10-16")
(True, '3 of 6 crossed a threshold: CAT (move -6.0%); XOM (move +4.6%, volume 2.6x avg); JNJ (earnings in 3 d)')
>>> wake(screen_rows([quote("PEP", -1.11)], rules), as_of_date="2026-10-16", today="2026-10-16")
(False, 'quiet: none of 1 tickers crossed a threshold')
```

A day without a session, like Thanksgiving, is a no even when the last session moved: those moves were reported on the day they happened.

```python
>>> wake(screen_rows([quote("CAT", -6.0)], rules), as_of_date="2026-11-25", today="2026-11-26")
(False, 'no session today (latest data is from 2026-11-25)')
```

With no tickers in either list, the scan skips without a word: there is nothing to screen.

No data for any ticker, even after the retry, is a no that is posted in the agent's channel, at most once a day ("Yahoo Finance: no data for any of the 1 tickers, after a retry"): Yahoo may be down, or its API may have changed. A network error raised during the fetch is only logged, and the next run retries.

```python
>>> wake([{"symbol": "PEP", "error": "ValueError: No price data returned"}], as_of_date=None, today="2026-10-16")
(False, 'market data unavailable for all 1 tickers')
```

Otherwise, what the scan couldn't read rides along with its findings on a day it wakes the agent, and is posted in the agent's channel on a day it doesn't. The findings are the screen as a table, in the screen's order:

```python
>>> from agents.market_scout.lib.triage import screen_table
>>> print(screen_table(screen_rows([quote("XOM", 4.6, ratio=2.6), {"symbol": "NOPE", "error": "x"}], rules)))
| Symbol | Price | Δ% | Vol × Avg | Next Earnings | Crossed |
| :--- | ---: | ---: | ---: | :--- | :--- |
| XOM | USD 100.00 | +4.60% | 2.6x | — | move +4.6%, volume 2.6x avg |
| NOPE | — | — | — | — | data unavailable |
```
