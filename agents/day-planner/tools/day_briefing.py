"""day_briefing: today's planning briefing, the one the 06:00 morning plan starts from."""
from core.tooling import Report, agent_tool, get_local_now, load_rules
from agents.day_planner.lib.briefing import build_briefing
from agents.day_planner.lib.rules import RULES


@agent_tool
def day_briefing(report: Report) -> str:
    """Today's planning briefing, the one the 06:00 morning plan starts from. Call it when it isn't
    in this conversation yet: to plan the day, to log the plan, or for "what's on today?". Read-only.

    Reads: daily and weekly notes in pkm/vault/journals/, the daily template, tasks.db, projects.db, RULES.md.
    Computes: today's date, ISO and vault weeks; recurring chores due; open tasks due within 7 days or slipped; this year's active projects by priority, each with its top 3 open tasks.
    Leaves out: what the notes say (yesterday's and today's notes and this week's focus come back as written); the plan and its priorities.
    """
    rules = load_rules("day-planner", RULES, report)
    return build_briefing(get_local_now(), report, rules, include_new_note=True)
