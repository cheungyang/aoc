"""weekend_availability: the next weekends from the calendar, ready to plan around."""
from core.tooling import Report, agent_tool, get_local_now, load_rules, rules_path
from tools import gog
from agents.excursion_planner.lib.events import CALENDAR, parse_events
from agents.excursion_planner.lib.rules import RULES
from agents.excursion_planner.lib.weekends import MAX_WEEKS, free_time, query_range
from agents.excursion_planner.lib.weekends import weekend_availability as plan_weekends

AGENT_ID = "excursion-planner"
# Events fetched per week of the range; more than that is reported, not dropped silently.
EVENTS_PER_WEEK = 100


@agent_tool
def weekend_availability(report: Report, weeks: int = 3) -> dict:
    """Your calendar for the next weekends, ready to plan around, in local time.

    Reads: your primary Google Calendar; RULES.md for when free time counts.
    Computes: which weekends (this one until its Saturday is over); each day's events
    ("(free)" if shown as free), all-day events ("(busy)" if marked busy, the only ones
    that block the day), free windows and free hours, today's from now; a note on a
    clock change.
    Leaves out: cancelled and declined events, working-location markers, and what to pitch.

    Args:
        weeks: How many weekends, 1–8 (default 3).
    """
    free = free_time(load_rules(AGENT_ID, RULES, report), report, rules_path(AGENT_ID))
    weeks = max(1, min(MAX_WEEKS, int(weeks)))
    now = get_local_now()
    start, end = query_range(now.date(), weeks, now.tzinfo)
    limit = EVENTS_PER_WEEK * weeks
    report.trace(f"{CALENDAR}: asked for up to {limit} events from {start:%Y-%m-%dT%H:%M:%SZ} "
                 f"to {end:%Y-%m-%dT%H:%M:%SZ}")
    try:
        payload = gog.list_events(start, end, max_results=limit)
    except gog.GogError as e:
        payload = None
        report.missing(CALENDAR, str(e))
    report.raise_if_failed()
    events = parse_events(payload, now.tzinfo, report, limit)
    report.raise_if_failed()
    return plan_weekends(events, now, weeks, free, report)
