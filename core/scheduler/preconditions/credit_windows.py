"""credit_windows: reward-travel speaks up only when a card credit needs using."""
from core.scheduler.preconditions.scan import Scan
from core.scheduler.spec import ScheduleContext, WorkDecision


class CreditWindows(Scan):
    """A recurring card credit is falling behind or about to lapse.

    Findings: those credits, with their period end and days left.
    """
    type_name = "credit_windows"

    def scan(self, ctx: ScheduleContext) -> WorkDecision:
        raise NotImplementedError("credit_windows is not built yet")
