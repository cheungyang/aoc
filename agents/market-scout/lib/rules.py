"""market-scout's thresholds. The user's values live in pkm/agents/market-scout/RULES.md.

The market_screen tool and scan read them with `load_rules`; WORKFLOW.md says
how each one is applied, with worked examples.
"""
from core.tooling import Rule

RULES = (
    Rule("move_pct", 3.0, "daily move, in % up or down, at which a ticker is flagged"),
    Rule("earnings_days", 7, "days before earnings at which a ticker is flagged (0: only on the day)"),
    Rule("volume_ratio", 2.0, "volume, as a multiple of its 20-session average, at which a ticker is flagged"),
)
