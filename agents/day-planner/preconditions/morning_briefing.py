"""morning_briefing: day-planner's morning plan starts from a briefing computed in code."""
from core.scheduler.preconditions.scan import Scan
from core.scheduler.spec import ScheduleContext, WorkDecision
from core.tooling import Report, load_rules


class MorningBriefing(Scan):
    """Always runs; hands the morning plan its dates, chores, tasks and notes.

    Findings: the briefing (`agents.day_planner.lib.briefing`), built for the
    run's local date from the daily and weekly notes, tasks.db and projects.db,
    with the thresholds in RULES.md. What it couldn't read rides along as issues.
    """
    type_name = "morning_briefing"

    def scan(self, ctx: ScheduleContext, report: Report) -> WorkDecision:
        from datetime import datetime

        from core.util.local_time import get_local_timezone
        from agents.day_planner.lib.briefing import build_briefing
        from agents.day_planner.lib.rules import RULES

        now = datetime.fromtimestamp(ctx.now, get_local_timezone())
        rules = load_rules(ctx.agent_id, RULES, report)
        return WorkDecision(True, f"morning briefing for {now:%a %Y-%m-%d}", detail=build_briefing(now, report, rules))
