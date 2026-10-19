"""market_screen: market-scout's daily scan runs only when a ticker moved enough to report."""
from core.scheduler.preconditions.base import PreconditionError
from core.scheduler.preconditions.scan import Scan
from core.scheduler.spec import ScheduleContext, WorkDecision
from core.tooling import Report


class MarketScreen(Scan):
    """A portfolio or watchlist ticker crossed one of market-scout's thresholds today.

    Runs the market_screen tool's screen over pkm/wallet/stock/portfolio.md and
    watchlist.md, with the thresholds in pkm/agents/market-scout/RULES.md: the
    same free Yahoo quotes the agent's first step fetched. Says yes only when a
    ticker crossed a threshold in today's session. A day without a session (a
    market holiday) is a no: the moves it shows were reported on the day they
    happened. Lists that hold no tickers, both even, are a quiet no: nothing
    to screen. No data for any ticker, even after the retry, is a no with an
    issue, so it's posted (once a day): Yahoo may be down, or its API may have
    changed. A network error raised during the fetch is only logged, and the
    next run retries.

    Findings: the screen table, tickers that crossed first with what they
    crossed, then those without data and the rest, which the reply also lists.
    """
    type_name = "market_screen"

    def validate(self):
        super().validate()
        if self.args:
            raise PreconditionError("'market_screen' takes no args: its thresholds are in pkm/agents/market-scout/RULES.md")

    def scan(self, ctx: ScheduleContext, report: Report) -> WorkDecision:
        from datetime import datetime

        from core.tooling import get_local_timezone, load_rules
        from agents.market_scout.lib.quotes import YAHOO, no_data, screen_quotes
        from agents.market_scout.lib.rules import RULES
        from agents.market_scout.lib.tickers import followed_tickers
        from agents.market_scout.lib.triage import any_data, screen_table, thresholds_text, wake

        rules = load_rules(ctx.agent_id, RULES, report)
        followed = followed_tickers(report)
        if not followed:
            return WorkDecision(False, "nothing to screen: portfolio.md and watchlist.md list no tickers")
        quotes = Report()
        screen = screen_quotes(followed, rules, quotes)
        rows = screen["results"]
        if any_data(rows):
            report.merge(quotes)
        else:  # one issue says it: the per-ticker ones would only repeat it
            report.problem(YAHOO, no_data(len(rows)))
        today = datetime.fromtimestamp(ctx.now, get_local_timezone()).date().isoformat()
        has_work, reason = wake(rows, screen["as_of_date"], today)
        if not has_work:
            return WorkDecision(False, reason)
        findings = (f"{screen['crossed_any']} of {len(rows)} tickers crossed a threshold ({thresholds_text(rules)}) "
                    f"in the {today} session.\n\n{screen_table(rows)}")
        return WorkDecision(True, reason, detail=findings)
