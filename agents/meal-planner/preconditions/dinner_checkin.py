"""dinner_checkin: the 21:00 dinner question runs only while tonight's dinner isn't logged."""
from core.scheduler.preconditions.scan import Scan
from core.scheduler.spec import ScheduleContext, WorkDecision
from core.tooling import Report


class DinnerCheckin(Scan):
    """Tonight's dinner isn't in MEAL_LOGS.md yet, so the check-in question is worth asking.

    Reads pkm/wallet/kitchen/MEAL_LOGS.md for a 晚餐 entry dated today, local
    time (`agents.meal_planner.lib.meal_log.logged_meal`); ideas don't count.
    Skips the run when there is one. Otherwise the question runs, and anything
    it couldn't read rides along as issues: a missing file, or lines in
    today's sections it doesn't recognize (dinner may be on one of them).

    Findings: none; the question needs nothing from the scan.
    """
    type_name = "dinner_checkin"

    def scan(self, ctx: ScheduleContext, report: Report) -> WorkDecision:
        from datetime import datetime

        from core.tooling import get_local_timezone
        from agents.meal_planner.lib.kitchen_io import MEAL_LOGS, kitchen_path, read_text
        from agents.meal_planner.lib.meal_log import logged_meal, unrecognized

        today = datetime.fromtimestamp(ctx.now, get_local_timezone()).date()
        path = kitchen_path(MEAL_LOGS)
        text = read_text(path)
        if text is None:
            report.problem(path, "file not found, so tonight's dinner can't be checked")
            return WorkDecision(True, f"no meal log to check for {today.isoformat()}")
        dinner = logged_meal(text, today)
        if dinner:
            return WorkDecision(False, f"dinner already logged for {today.isoformat()} ({dinner})")
        report.unparsed(path, unrecognized(text, today, 1))
        return WorkDecision(True, f"no dinner logged for {today.isoformat()} yet")
