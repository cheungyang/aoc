"""software-planner's thresholds: when a waiting PR is raised, and how many fixes a task gets.

The user's values live in pkm/agents/software-planner/RULES.md, over these defaults
(core/tooling/rules.py). WORKFLOW.md says how each one is used.
"""
from core.tooling import Rule

RULES = (
    Rule("remind_after_hours", 24,
         "hours a PR waits after the tick stops checking it before Elephant reminds you, and between reminders "
         "(0: never)"),
    Rule("max_fixes_per_task", 2, "fixes Elephant makes to one task before he hands it to you"),
)
