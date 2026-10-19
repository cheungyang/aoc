"""day-planner's thresholds: the defaults behind pkm/agents/day-planner/RULES.md (core/tooling/rules.py)."""
from core.tooling import Rule

RULES = (
    Rule("chore_lead_days", 3, "days before its date a recurring chore with no completion history is flagged as due soon"),
)
