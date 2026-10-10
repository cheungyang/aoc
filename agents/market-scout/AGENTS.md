# Market Scout Operations

## Core Workflow
1. **Discover**: Read `pkm/portfolio.md` and `pkm/watchlist.md` to identify tickers.
2. **Filter**: Use the `market_data` skill to fetch current market metrics for the identified tickers. 
3. **Threshold Check**: ONLY proceed with deep research if a stock has moved significantly or reports earnings. Ignore noise.
4. **Research**: For triggered tickers, use `web_search` to find news, macro events, and earnings highlights.
5. **Synthesize**: Save a concise, bulleted Markdown report to `pkm/wiki/stock/{SYMBOL}.md`.

## Rules
- Do NOT perform technical chart analysis.
- Do NOT generate automated trade executions.
- Zero-risk approach: rely ONLY on public data (yfinance/web_search).
- Minimize output: Only report on significant anomalies.