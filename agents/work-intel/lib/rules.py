"""work-intel's thresholds: the defaults behind pkm/agents/work-intel/RULES.md (core/tooling/rules.py)."""
from core.tooling import Rule

RULES = (
    Rule("lookback_days", 3, "the number of recent daily journals a sparring session reads for context"),
)
