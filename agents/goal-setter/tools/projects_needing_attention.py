"""projects_needing_attention: the executing projects that most need coaching, ranked."""
from core.tooling import Report, agent_tool, get_local_now, load_rules
from agents.goal_setter.lib.attention import projects_needing_attention as rank
from agents.goal_setter.lib.rules import RULES
from agents.goal_setter.lib.snapshot import load_snapshot


@agent_tool
def projects_needing_attention(report: Report, limit: int = 5) -> dict:
    """The executing projects that most need coaching, ranked, each with its score and reasons.

    Reads: projects.db, tasks.db, each executing project's note and dossier, and RULES.md for the weights.
    Computes: each project's points for its signals (never or long ago coached, stalled, no open
    tasks, overdue tasks) plus its priority's; projects coached within rest_days rest.
    Leaves out: which project to coach, and how (your coach_project skill decides).

    Returns {"executing": count, "projects": [{"name", "priority", "score", "reasons",
    "last_coached", "open_tasks", "done_last_30d"}], "more": names past the limit,
    "coached_in_last_<rest_days>d": resting names}.

    Args:
        limit: how many projects to return in full (default 5).
    """
    rules = load_rules("goal-setter", RULES, report)
    snapshot = load_snapshot(report)
    return rank(get_local_now().date(), rules, snapshot, report, limit=max(1, int(limit)))
