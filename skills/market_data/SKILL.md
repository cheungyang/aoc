---
name: market_data
description: Screen stock tickers with the yfinance tool, apply the Threshold Rule to decide which ones deserve deep research, and produce a compact triage table.
---
## Overview
This skill decides **which tickers deserve attention today**. It uses the `yfinance` tool for clean, structured market data, so you don't need unstructured web searches for prices. Then it applies a fixed Threshold Rule that separates real signals from noise. Deep research (e.g. `web_search`) is spent **only** on tickers that cross the threshold.

> The `yfinance` tool's own description covers its arguments, output fields and units. This skill covers *when* to call it and *what to do* with the results.

## Workflow

### 1. Screen: one batch `quote` call
Collect every ticker you need to check and pass them **all in a single call**:

```
yfinance(action="quote", tickers=["AAPL", "TSLA", "MSFT", ...])
```

- Never loop over tickers one at a time. The tool batches internally.
- Don't re-query a ticker you already have results for in this run.

### 2. Apply the Threshold Rule
A ticker is **Triggered** if ANY of these is true:

| Trigger | Condition | Reason label |
| :--- | :--- | :--- |
| Price move | `abs(daily_movement_pct) >= 3.0` | `move ±X.X%` |
| Earnings soon | `days_to_earnings` is not null and `0 <= days_to_earnings <= 7` | `earnings in N d` |
| Volume spike | `volume_ratio >= 2.0` | `volume X.Xx avg` |

If several triggers fire, list all the reason labels. Otherwise the ticker is **Stable**: skip all further research for it to save tokens.

A ticker with an `error` field is **Error**. Retry it once in a single follow-up `quote` call together with any other failed tickers. If it still fails, report it as "data unavailable" and move on. Don't guess its values.

### 3. Drill down (Triggered tickers only, optional)
Use these only when they add context to the research:
- **Price move or volume spike** → `yfinance(action="history", tickers=[...triggered...], period="1mo")` shows whether the move is part of a trend or a one-day spike (`period_change_pct`, `period_high`/`period_low`).
- **Earnings soon** → `yfinance(action="earnings", tickers=[...triggered...])` shows the last 4 EPS surprises, which sets expectations for the upcoming report.

Batch all triggered tickers into one call per action.

### 4. Hand off to research
For each Triggered ticker, research the *why* (news, macro events, earnings highlights) with `web_search`, guided by its trigger reason. Stable tickers get no research.

## Output Contract
Always produce this triage table (Triggered first, then Error, then Stable):

| Symbol | Price | Δ% | Vol × Avg | Next Earnings | Status | Trigger Reason |
| :--- | ---: | ---: | ---: | :--- | :--- | :--- |
| TSLA | 382.70 | +4.10% | 2.3x | 2026-10-21 (11d) | Triggered | move +4.1%, volume 2.3x avg |
| AAPL | 336.64 | -1.11% | 1.0x | 2026-11-02 (23d) | Stable | — |

## Guardrails
- No technical chart analysis (indicators, patterns, support/resistance).
- No buy/sell recommendations or trade execution.
- Use public data only (`yfinance`, `web_search`).
- Treat prices as possibly delayed. Mention `as_of_date` when it isn't today.