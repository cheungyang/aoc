"""credit_windows: reward-travel speaks up only when a card credit needs using."""
from core.scheduler.preconditions.scan import Scan
from core.scheduler.spec import ScheduleContext, WorkDecision
from core.tooling import Report


class CreditWindows(Scan):
    """A recurring card credit has newly fallen behind or is newly about to lapse.

    Each credit comes up at most twice a period: once when more of it than
    `behind_after_pct` (RULES.md) has gone unused, and again when its deadline
    is within `lapse_days` (see agents/reward-travel/lib/credits.py). A credit
    already reported at its stage on the last successful run isn't reported again.

    Findings: those credits, with their status, days left and period end, and
    when each card file was last updated.
    """
    type_name = "credit_windows"

    def scan(self, ctx: ScheduleContext, report: Report) -> WorkDecision:
        from datetime import datetime

        from core.tooling import get_local_timezone, load_rules, pkm_path, rules_path
        from agents.reward_travel.lib.credits import credit_windows, newly_flagged, thresholds
        from agents.reward_travel.lib.rules import RULES

        tz = get_local_timezone()
        today = datetime.fromtimestamp(ctx.now, tz).date()
        since = datetime.fromtimestamp(ctx.last_success_at, tz).date() if ctx.last_success_at else None
        limits = thresholds(load_rules(ctx.agent_id, RULES, report), report, rules_path(ctx.agent_id))
        summary = credit_windows(pkm_path("wallet", "credit-card"), today, limits, report)
        report.raise_if_failed()  # no card folder, or no credit table in it
        due = newly_flagged(summary, since, limits)
        if not due:
            earlier = len(summary.flagged)
            return WorkDecision(False, "no credit newly behind or lapsing" + (f" ({earlier} reported before)" if earlier else ""))

        lines = [f"Card credits that need using, as of {today.isoformat()}:"]
        lines += [f"- {window.describe()} [{window.flag}]" for window in due]
        dated = [f"{f.name} {f.last_updated}{' (stale)' if f.stale else ''}" for f in summary.files if f.last_updated]
        if dated:
            lines.append(f"Card files last updated: {'; '.join(dated)}.")
        lapsing = sum(1 for window in due if window.flag == "lapsing")
        reason = f"{len(due)} credit(s) need using ({lapsing} about to lapse)"
        return WorkDecision(True, reason, detail="\n".join(lines))
