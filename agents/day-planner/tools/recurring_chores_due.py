"""recurring_chores_due: recurring chores that need attention, judged by when the user usually does each one."""
from contextlib import closing

from core.tooling import Report, agent_tool, get_local_now, load_rules
from agents.day_planner.lib.recurring_chores import chores_due, format_chores
from agents.day_planner.lib.rules import RULES
from agents.day_planner.lib.task_cache import open_cache, tasks_db_path


@agent_tool
def recurring_chores_due(report: Report, within_days: int = 0) -> str:
    """Recurring chores (🔁) that need attention, judged by when the user usually does each one.
    Call it for "which chores are due?" or to check chores outside the morning briefing (which
    already includes this list). Read-only. Lines are ready to relay.

    Reads: tasks.db (pending 🔁 tasks and their completed copies), RULES.md.
    Computes: each chore's window: its date minus the most days early of its last 8 completions, or chore_lead_days with no history, never before its 🛫 start date; then overdue, due today, due soon or quiet, with day counts.
    Leaves out: which chores to do, and when.

    Args:
        within_days: Also list chores whose date is within this many days but whose usual
            window hasn't opened yet; 7 answers "this week". Defaults to 0 (flagged chores only).
    """
    rules = load_rules("day-planner", RULES, report)
    path = tasks_db_path()
    conn = open_cache(path, report, required=True)
    report.raise_if_failed()
    with closing(conn):
        chores = chores_due(conn, get_local_now().date(), rules["chore_lead_days"], report)
    return format_chores(chores, within_days=max(0, within_days))
