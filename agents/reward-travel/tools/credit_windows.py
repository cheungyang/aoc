"""credit_windows: where every recurring statement credit stands today."""
from core.tooling import Report, agent_tool, get_local_now, load_rules, pkm_path, rules_path
from agents.reward_travel.lib.credits import CreditWindow, thresholds
from agents.reward_travel.lib.credits import credit_windows as build
from agents.reward_travel.lib.rules import RULES

AGENT_ID = "reward-travel"


@agent_tool
def credit_windows(report: Report) -> dict:
    """Where every recurring statement credit stands today, from the card files in pkm/wallet/credit-card/.
    Call it instead of reading them for credits, their deadlines, or 5/24.

    Reads: their credit tables, Last Updated and 5/24 lines; RULES.md.
    Computes: each credit's period, end, days left and status (used, auto, missed, unused, partly used, or
    not logged = unused); for unused ones the flag "lapsing" (within `lapse_days` of the end) or "behind"
    (past `behind_after_pct` of the period); 5/24 counts; file ages (stale after `stale_after_months`).
    Leaves out: balances, anniversaries, text outside the credit tables; which credit to use, and how.

    Most urgent first; money with ISO codes. Returns JSON: today, credits [{credit, value, period, ends,
    days_left, status, remaining?, by?, notes?, flag?}], five_24 [{player, count, status}], files [{file,
    last_updated, months_old, stale}].
    """
    rules = load_rules(AGENT_ID, RULES, report)
    limits = thresholds(rules, report, rules_path(AGENT_ID))
    summary = build(pkm_path("wallet", "credit-card"), get_local_now().date(), limits, report)
    report.raise_if_failed()  # no card folder, or no credit table in it
    return {
        "today": summary.today.isoformat(),
        "credits": [_row(window) for window in summary.windows],
        "five_24": [{"player": p.player, "count": p.count, "status": p.text} for p in summary.five_24],
        "files": [
            {"file": f.name, "last_updated": f.last_updated, "months_old": f.months_old, "stale": f.stale}
            for f in summary.files
        ],
    }


def _row(window: CreditWindow) -> dict:
    row = {
        "credit": window.name,
        "value": window.value,
        "period": window.period.label if window.period else window.cycle or "unknown cycle",
        "ends": window.period.end.isoformat() if window.period else None,
        "days_left": window.days_left,
        "status": window.status,
    }
    if window.remaining:
        row["remaining"] = window.remaining
    if window.by:
        row["by"] = window.by
    if window.notes:
        row["notes"] = list(window.notes)
    if window.flag:
        row["flag"] = window.flag
    return row
