"""morning_briefing: day-planner's morning plan starts from a briefing computed in code."""
from core.scheduler.preconditions.scan import Scan
from core.scheduler.spec import ScheduleContext, WorkDecision


class MorningBriefing(Scan):
    """Always runs; hands the morning plan its dates, chores and fixed reads.

    Findings: the briefing.
    """
    type_name = "morning_briefing"

    def scan(self, ctx: ScheduleContext) -> WorkDecision:
        raise NotImplementedError("morning_briefing is not built yet")
