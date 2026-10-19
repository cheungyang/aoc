"""reward-travel's thresholds: the defaults behind pkm/agents/reward-travel/RULES.md.

Its tools and the credit-windows scan read them with `load_rules`; WORKFLOW.md
says where each one applies.
"""
from core.tooling import Rule

RULES = (
    Rule("cpp_alert_above", 2.0, "cents per point above which an award is worth an alert (band good)"),
    Rule("behind_after_pct", 50, "% of a credit's period gone, still unused, before it counts as behind"),
    Rule("lapse_days", {"monthly": 7, "quarterly": 14, "semiannual": 21, "annual": 30},
         "days before a credit's deadline when it counts as about to lapse, by cycle"),
    Rule("stale_after_months", 3, "months after a card file's Last Updated date before it counts as stale"),
)
