"""ha_audit: home-steward's daily audit runs only when Home Assistant has new problems."""
from core.scheduler.preconditions.scan import Scan
from core.scheduler.spec import ScheduleContext, WorkDecision
from core.tooling import Report

# With no successful audit yet, "new" means the last day.
FIRST_RUN_LOOKBACK_SECONDS = 24 * 60 * 60


class HaAudit(Scan):
    """Home Assistant has a new problem to report: new errors, or entities that
    turned unavailable or unknown.

    New since the last successful audit (the last day on the first run).
    Recoveries and problems that were already there don't wake the audit.
    Read-only and nothing stored; see agents/home-steward/lib/homeassistant/audit.py
    for how each is decided, and the restart and history-purge edge cases.

    Findings: the new problems, then counts of the ongoing ones.
    """
    type_name = "ha_audit"

    def scan(self, ctx: ScheduleContext, report: Report) -> WorkDecision:
        from core.util.local_time import get_local_timezone
        from agents.home_steward.lib.homeassistant import audit

        since = ctx.last_success_at
        if since is None:
            since = ctx.now - FIRST_RUN_LOOKBACK_SECONDS
        news = audit.check(since, get_local_timezone())
        if not news.findings:
            return WorkDecision(False, f"no new problems ({news.summary})")
        return WorkDecision(True, news.summary, detail=news.findings)
