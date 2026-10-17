"""ha_audit: home-steward's daily audit runs only when Home Assistant has news."""
from core.scheduler.preconditions.scan import Scan
from core.scheduler.spec import ScheduleContext, WorkDecision


class HaAudit(Scan):
    """Home Assistant has something new to report: new errors, or a change in
    unavailable entities.

    Findings: the issues.
    """
    type_name = "ha_audit"

    def scan(self, ctx: ScheduleContext) -> WorkDecision:
        raise NotImplementedError("ha_audit is not built yet")
