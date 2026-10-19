---
name: market_data
description: Screen the portfolio and watchlist with market_screen, decide each ticker's verdict and what to research, and reply with a compact triage table.
---
## Overview
`market_screen` returns facts: each ticker's price, move, volume against its average, next earnings, and the thresholds it crossed (`crossed`; the values in use are in `thresholds`), already sorted. Take its numbers and crossings as given; if one looks wrong, check it with the `tool_audit` skill. The verdict and the research are yours, decided here.

## Verdict and research
- **Triggered**: `crossed` lists a threshold. **Quiet**: it lists none. **Unavailable**: the row has an `error` (already retried once); say "data unavailable", never guess its values.
- Spend research (`web_search`) **only** on Triggered tickers. Quiet tickers get none.

## Workflow

### Check (on request, e.g. "how are my stocks doing?")
1. Call `market_screen` once, with no arguments: it reads `portfolio.md` and `watchlist.md` itself. Pass `tickers` only for symbols outside those lists.
2. Reply with the triage table. Offer to research the Triggered tickers; research only when asked. If the screen is empty (`count` 0), reply instead in one line that portfolio.md and watchlist.md list no tickers, and offer to add some.

### Daily scan (scheduled)
The run starts with the screen's findings: don't screen again.
1. In one step, `web_search` the *why* for each Triggered ticker (news, macro events, earnings), guided by what it crossed. Add a `yfinance` drill-down in the same step only when it adds context, with all Triggered tickers in one call per action:
   - Move or volume → `history` (`period="1mo"`): part of a trend, or a one-day spike?
   - Earnings → `earnings`: the last 4 EPS surprises set expectations.
2. Append each Triggered ticker's entry to `pkm/wallet/stock/{SYMBOL}.md` in one `filesystem` call, then reply with the triage table and one line on each Triggered ticker's *why*.

## Output Contract
Always produce the triage table, rows in the screen's order; Trigger Reason is the `crossed` list:

| Symbol | Price | Δ% | Vol × Avg | Next Earnings | Status | Trigger Reason |
| :--- | ---: | ---: | ---: | :--- | :--- | :--- |
| XOM | USD 112.30 | +4.60% | 2.6x | 2026-10-28 (12d) | Triggered | move +4.6%, volume 2.6x avg |
| PEP | USD 152.18 | -1.11% | 1.0x | 2026-11-02 (17d) | Quiet | — |

The daily entry, appended with `append` and ending with a newline:

```markdown
## 2026-10-16 · move +4.6%, volume 2.6x avg
- USD 112.30 (+4.60%), volume 2.6x avg, next earnings 2026-10-28 (12d)
- Why: <one to three bullets from the research>
```

## Guardrails
- No technical chart analysis (indicators, patterns, support/resistance).
- No buy/sell recommendations or trade execution.
- Use public data only (`market_screen`, `yfinance`, `web_search`).
- Minimize output: only significant anomalies get commentary beyond the triage table.
- Treat prices as possibly delayed. Mention the screen's `as_of_date` when `as_of_today` is false.