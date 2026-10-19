"""excursion-planner's thresholds: when weekend free time counts.

The user's values live in pkm/agents/excursion-planner/RULES.md, over these
defaults (core/tooling/rules.py). WORKFLOW.md says how each one is used.
"""
from core.tooling import Rule

RULES = (
    Rule("free_from", "08:00", "free time on a weekend day counts from this local time, 24-hour HH:MM"),
    Rule("free_until", "22:00", "free time on a weekend day counts until this local time, 24-hour HH:MM"),
    Rule("min_gap_hours", 1.0, "the shortest free gap worth pitching, in hours"),
)
