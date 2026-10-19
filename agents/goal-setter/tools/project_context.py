"""project_context: everything a coaching session on one project needs, in one read."""
from core.tooling import Report, ToolError, agent_tool, get_local_now
from agents.goal_setter.lib.attention import ProjectNotFound
from agents.goal_setter.lib.attention import project_context as build_context
from agents.goal_setter.lib.snapshot import load_snapshot


@agent_tool
def project_context(report: Report, name: str) -> dict:
    """Everything a coaching session on one project needs, in one call: its details, coaching
    history (last coached, dossier notes, the latest session), task counts and velocity, open
    tasks (most urgent first) and the last 30 days' completions.

    Reads: projects.db, tasks.db, the project's note, and its dossier in pkm/wiki/project-coaching/.
    Computes: days since last coached; open, overdue and due-in-14-days counts; tasks done in the
    last 7, 30 and 90 days, per week, and the trend.
    Leaves out: completed tasks with no completion date from velocity (`completed_without_date`
    counts them); notes past 1,200 characters (cut with …).

    Call it once at the start of coaching prep and relay its numbers; don't recompute them.
    When no single project matches, the error lists the candidates.

    Args:
        name: the project name, a unique part of it, or its alias tag ('10k', '#p/alpha').
    """
    snapshot = load_snapshot(report)
    try:
        return build_context(name, get_local_now().date(), snapshot, report)
    except ProjectNotFound as e:
        raise ToolError(str(e)) from None
