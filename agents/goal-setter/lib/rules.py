"""goal-setter's thresholds: how executing projects are ranked for coaching.

The user's values live in pkm/agents/goal-setter/RULES.md, over these defaults
(core/tooling/rules.py). WORKFLOW.md says how each one is used.
"""
from core.tooling import Rule

RULES = (
    Rule("rest_days", 7, "days a coached project rests: it isn't ranked again sooner"),
    Rule("coach_every_days", 14, "days after a coaching when the project is due again"),
    Rule("long_gap_days", 28, "days after a coaching that make a long gap"),
    Rule("stall_days", 30, "days with open tasks and none done before a project counts as stalled"),
    Rule("never_coached", 3, "points for a project never coached"),
    Rule("due_again", 2, "points when the last coaching was coach_every_days ago or more"),
    Rule("long_gap", 3, "points instead when it was long_gap_days ago or more"),
    Rule("stalled", 2, "points for a stalled project"),
    Rule("no_open_tasks", 2, "points for a project with no open tasks"),
    Rule("overdue", 1, "points for a project with an overdue task"),
    Rule("priority_points", {"🔺": 3, "⏫": 2, "🔼": 1, "🔽": 0, "⏬": 0},
         "points by the priority of this year's commitment"),
)
