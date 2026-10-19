"""market_screen: the portfolio and watchlist against market-scout's thresholds, in one call."""
from typing import List, Optional

from core.tooling import Report, agent_tool, load_rules
from agents.market_scout.lib.quotes import YAHOO, no_data, screen_quotes
from agents.market_scout.lib.rules import RULES
from agents.market_scout.lib.tickers import followed_tickers, requested_tickers
from agents.market_scout.lib.triage import any_data


@agent_tool
def market_screen(report: Report, tickers: Optional[List[str]] = None) -> dict:
    """Screen the portfolio and watchlist in one call: the latest session's numbers, and the thresholds each ticker crossed.

    Reads: pkm/wallet/stock/portfolio.md and watchlist.md ('- SYMBOL' bullets), Yahoo Finance quotes, RULES.md.
    Computes: each ticker's price with its currency code, daily move % (3.2 means +3.2%), volume against its 20-session average, days to earnings, and the thresholds it crossed (`crossed`, against `thresholds`); retries failed quotes once.
    Leaves out: the verdict and what to research (your market_data skill), the news, and earlier sessions.

    Results: tickers that crossed first, then those without data ('error'), then the rest, biggest move
    first; none (count 0) when the lists hold no tickers. Prices may be delayed: see as_of_date and as_of_today.

    Args:
        tickers: Screen these symbols instead of the lists, e.g. ["KO", "PEP"]; max 50.
    """
    rules = load_rules("market-scout", RULES, report)
    followed = requested_tickers(tickers) if tickers else followed_tickers(report)
    screen = screen_quotes(followed, rules, report)
    if screen["results"] and not any_data(screen["results"]):
        report.missing(YAHOO, no_data(screen["count"]))
        report.raise_if_failed()
    return screen
