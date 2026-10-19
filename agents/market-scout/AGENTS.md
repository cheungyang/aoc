# Market Scout Operations

## Workflows
1. **Check** (on request, e.g. "how are my stocks doing?"): the `market_data` skill's check, one `market_screen` call, then the triage table.
2. **Daily scan** (scheduled): the run arrives with the screen's findings; research and log only the Triggered tickers, per the skill's daily scan.
3. **Research a ticker** (on request): `market_screen` with `tickers`, plus `yfinance` (`history`, `earnings`) and `web_search`, all in one step; then save a concise, bulleted Markdown report to `pkm/wallet/stock/{SYMBOL}.md`.
4. **Edit the lists**: add or remove tickers in `pkm/wallet/stock/portfolio.md` or `watchlist.md`, one `- SYMBOL` bullet per line. The screen reads only those bullets, on every run; never hard-code tickers. Lines it can't read come back raw in its issues: offer to rewrite them as bullets.
5. **Thresholds** (e.g. "alert me on a 4% move"): RULES.md keys `move_pct`, `earnings_days` and `volume_ratio`; the screen's `thresholds` shows the values in use.