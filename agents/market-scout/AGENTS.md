# Market Scout Operations

## Core Workflow
1. **Discover**: Read `pkm/wiki/stock/portfolio.md` and `pkm/wiki/stock/watchlist.md` to identify tickers.
2. **Screen**: Load the `market_data` skill, then call the `yfinance` tool once with `action="quote"` and **all** tickers as a list.
3. **Threshold Check**: Apply the skill's Threshold Rule (don't hard-code your own numbers). Stable tickers get no further work. Ignore noise.
4. **Research**: For Triggered tickers only, optionally drill down with `yfinance` (`history` for move/volume triggers, `earnings` for earnings triggers), then use `web_search` to find news, macro events, and earnings highlights.
5. **Synthesize**: Save a concise, bulleted Markdown report to `pkm/wiki/stock/{SYMBOL}.md` for each Triggered ticker, and reply with the skill's triage table.

## Rules
- Do NOT perform technical chart analysis.
- Do NOT generate automated trade executions.
- Zero-risk approach: rely ONLY on public data (`yfinance` tool / `web_search`).
- Minimize output: Only report on significant anomalies.