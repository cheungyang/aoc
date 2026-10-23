"""calendar_add: one event on the user's Google Calendar, from local times.

`event_times` turns the local start and end into the times `gog calendar
create` takes: UTC for a timed event, daylight saving included, or an all-day
span whose end Google wants exclusive. The event is created through the gog
tool's helpers (tools/gog.py).
"""
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Tuple

from langchain_core.tools import tool

from core.loaders.tools_loader import ToolsLoader
from core.runtime.foundation.context import try_context
from core.util import format_tool_response
from core.util.local_time import (
    LocalTimeError,
    clock_change,
    day_label,
    describe_days,
    describe_span,
    localize,
    parse_local,
    utc_text,
)
from core.util.time_util import get_local_timezone
from tools import gog

# Creating an event is this tool's only action, and it writes.
READ_ACTIONS = ()


@dataclass(frozen=True)
class EventTimes:
    all_day: bool
    start: str  # --from: UTC RFC3339, or the first day
    end: str    # --to: UTC RFC3339, or the day after the last (Google's end date is exclusive)
    when: str   # the local span, as the user reads it
    notes: Tuple[str, ...] = ()


def event_times(local_start: str, local_end: str, tz: tzinfo) -> EventTimes:
    """Converts local times ('2026-10-24 09:00') to UTC, or dates to an all-day span
    whose `local_end` is the last day, inclusive. Raises LocalTimeError for an end
    before the start, a mix of a date and a time, or a time DST skips."""
    start, end = parse_local(local_start), parse_local(local_end)
    if isinstance(start, datetime) != isinstance(end, datetime):
        raise LocalTimeError(
            "give both as times ('2026-10-24 09:00'), or both as dates for an all-day event"
        )
    if not isinstance(start, datetime):
        if end < start:
            raise LocalTimeError(f"the last day ({end}) is before the first ({start})")
        return EventTimes(True, start.isoformat(), (end + timedelta(days=1)).isoformat(), describe_days(start, end))

    start_at, start_note = localize(start, tz)
    end_at, end_note = localize(end, tz)
    if end_at <= start_at:
        raise LocalTimeError(
            f"the end ({_moment(end_at, tz)}) must be after the start ({_moment(start_at, tz)})"
        )
    notes = [note for note in (start_note, end_note) if note]
    change = clock_change(start_at, end_at, tz)
    if change:
        notes.append(change)
    return EventTimes(False, utc_text(start_at), utc_text(end_at), describe_span(start_at, end_at, tz), tuple(notes))


def _moment(moment: datetime, tz: tzinfo) -> str:
    local = moment.astimezone(tz)
    return f"{day_label(local.date())} {local:%H:%M}"


@tool
def calendar_add(
    local_start: str,
    local_end: str,
    title: str,
    location: str = "",
    description: str = "",
    calendar: str = "primary",
) -> str:
    """Add an event to the user's Google Calendar, given in local time. It converts to
    UTC itself, daylight saving included, so pass times as the user says them.

    Call it only after the user approves the plan. Times are 'YYYY-MM-DD HH:MM', local.
    For an all-day event pass dates only ('YYYY-MM-DD'); local_end is then the last
    day, inclusive. A time the clocks skip is refused; one that happens twice (the
    night they fall back) is booked at its first occurrence, with a note.

    Returns the event as created: "when" (e.g. "Sat Oct 24, 09:00–13:00 PDT"), the
    UTC times sent, its link, and any DST notes.

    Args:
        local_start: the start ('2026-10-24 09:00'), or the first day of an all-day event.
        local_end: the end ('2026-10-24 13:00'), or the last day of an all-day event.
        title: the event title.
        location: a place name or address, optional.
        description: notes such as the booking link, optional.
        calendar: a calendar ID or name; defaults to the primary calendar.
    """
    ctx = try_context()
    if ctx is None:
        return format_tool_response(
            "calendar_add", payload="",
            errors="Error: no active execution context; this tool must be called from an agent run.",
        )
    if not ToolsLoader().check_permission(ctx, "calendar_add", "create"):
        return format_tool_response(
            "calendar_add", payload="",
            errors=f"Error: Agent {ctx.agent_id} may not create calendar events.",
        )
    if not str(title or "").strip():
        return format_tool_response("calendar_add", payload="", errors="Error: 'title' is required.")
    try:
        times = event_times(local_start, local_end, get_local_timezone())
        created = gog.create_event(
            calendar or "primary", title.strip(), times.start, times.end,
            all_day=times.all_day, location=(location or "").strip(), description=(description or "").strip(),
        )
        return format_tool_response(
            "calendar_add", payload=json.dumps(_summary(title.strip(), calendar or "primary", times, created), ensure_ascii=False)
        )
    except Exception as e:
        return format_tool_response("calendar_add", payload="", errors=f"Error in calendar_add: {e}")


def _summary(title: str, calendar: str, times: EventTimes, created) -> dict:
    event = created.get("event", created) if isinstance(created, dict) else {}
    summary = {"created": title, "when": times.when, "calendar": calendar}
    if not times.all_day:
        summary["utc"] = f"{times.start} → {times.end}"
    for key, field in (("link", "htmlLink"), ("id", "id")):
        if isinstance(event, dict) and event.get(field):
            summary[key] = event[field]
    if times.notes:
        summary["notes"] = list(times.notes)
    return summary
