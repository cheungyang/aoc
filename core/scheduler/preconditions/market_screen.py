"""market_screen: market-scout's daily scan runs only when a ticker moved enough to report."""
from core.scheduler.preconditions.scan import Scan
from core.scheduler.spec import ScheduleContext, WorkDecision


class MarketScreen(Scan):
    """A portfolio or watchlist ticker crossed a market_data threshold.

    Findings: the triggered tickers, each with its trigger reasons.
    """
    type_name = "market_screen"

    def scan(self, ctx: ScheduleContext) -> WorkDecision:
        raise NotImplementedError("market_screen is not built yet")
