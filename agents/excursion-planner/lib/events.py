"""gog's calendar event JSON -> CalendarEvent.

`gog calendar events --json` prints {"events": [...], "nextPageToken": ...}, each
event a Google Calendar Event resource. Only what availability needs is kept:
the title, whether it blocks time, and when it is.

Every event is accounted for: it is kept, left out on purpose (cancelled,
declined, a working-location marker), or it can't be read and comes back raw in
the report, so the model can still read it.
"""
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from typing import Any, List, Optional
from zoneinfo import ZoneInfo

from core.tooling import Report

# The source the tool's issues and explain lines name.
CALENDAR = "Google Calendar (primary)"
# Markers rather than plans: "working from home" books nothing.
IGNORED_EVENT_TYPES = {"workingLocation": "working location"}
# An unreadable event comes back as one JSON line, the fields read here first, cut at this length.
MAX_RAW = 400
_FIELDS_READ = ("summary", "start", "end", "status", "eventType", "transparency")


@dataclass(frozen=True)
class CalendarEvent:
    title: str
    busy: bool                          # False when it shows as free (transparent)
    start: Optional[datetime] = None    # timed events: aware instants
    end: Optional[datetime] = None
    first_day: Optional[date] = None    # all-day events: the first day,
    end_day: Optional[date] = None      # and the day after the last (exclusive)

    @property
    def all_day(self) -> bool:
        return self.first_day is not None


def parse_events(payload: Any, tz: tzinfo, report: Report, limit: Optional[int] = None) -> List[CalendarEvent]:
    """The events in gog's JSON, minus cancelled ones, declined invitations and
    working-location markers. `tz` reads any time that comes without an offset.

    A reply that holds no event list is missing data (`failed`). An event that
    can't be read is left out and goes in `report` raw (`partial`), and so does
    a reply with more events than the `limit` fetched.
    """
    items = _event_list(payload, report)
    events, left_out, unread = [], [], []
    for raw in items:
        try:
            why = _left_out(raw)
            if why:
                left_out.append(f"'{_title(raw)}' ({why})")
            else:
                events.append(parse_event(raw, tz))
        except (ValueError, TypeError, KeyError):
            unread.append(_raw_line(raw))
    report.unparsed(CALENDAR, unread)
    if isinstance(payload, dict) and payload.get("nextPageToken"):
        fetched = f"the {limit} events fetched" if limit else "the events fetched"
        report.problem(CALENDAR, f"it holds more than {fetched}; some may be missing")
    report.read(CALENDAR, records=len(events), skipped=len(left_out) + len(unread))
    if left_out:
        report.trace(f"left out: {', '.join(left_out)}")
    return events


def parse_event(raw: Any, tz: tzinfo) -> CalendarEvent:
    """One event that counts. Raises ValueError (or TypeError, KeyError) when it can't be read."""
    if not isinstance(raw, dict):
        raise ValueError("not an event")
    busy = raw.get("transparency") != "transparent"
    start, end = raw.get("start") or {}, raw.get("end") or {}
    if not isinstance(start, dict) or not isinstance(end, dict):
        raise ValueError("start and end must be objects")
    if start.get("date"):
        first = date.fromisoformat(start["date"])
        end_day = date.fromisoformat(end["date"]) if end.get("date") else first
        return CalendarEvent(_title(raw), busy, first_day=first, end_day=max(end_day, first + timedelta(days=1)))
    if start.get("dateTime"):
        start_at = _instant(start, tz)
        end_at = _instant(end, tz) if end.get("dateTime") else start_at
        return CalendarEvent(_title(raw), busy, start=start_at, end=max(end_at, start_at))
    raise ValueError("no start date or time")


def _event_list(payload: Any, report: Report) -> list:
    """The reply's events. gog may leave an empty list out (or null); a reply that
    holds data but no event list can't be read."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        items = payload.get("events", payload.get("items"))
        if isinstance(items, list):
            return items
        others = (value for key, value in payload.items() if key not in ("events", "items"))
        if items is None and not any(isinstance(value, list) and value for value in others):
            return []
    report.missing(CALENDAR, f"gog's reply holds no event list: {_raw_line(payload)}")
    return []


def _left_out(raw: Any) -> Optional[str]:
    """Why an event doesn't count, or None when it does."""
    if not isinstance(raw, dict):
        return None
    if raw.get("status") == "cancelled":
        return "cancelled"
    if raw.get("eventType") in IGNORED_EVENT_TYPES:
        return IGNORED_EVENT_TYPES[raw["eventType"]]
    if _declined(raw):
        return "declined"
    return None


def _title(raw: dict) -> str:
    # Events from calendars shared as free/busy only come without a summary.
    return str(raw.get("summary") or "").strip() or "(busy)"


def _declined(raw: dict) -> bool:
    return any(
        a.get("self") and a.get("responseStatus") == "declined"
        for a in raw.get("attendees") or []
        if isinstance(a, dict)
    )


def _instant(when: dict, tz: tzinfo) -> datetime:
    moment = datetime.fromisoformat(when["dateTime"])
    if moment.tzinfo is None:
        zone = ZoneInfo(when["timeZone"]) if when.get("timeZone") else tz
        moment = moment.replace(tzinfo=zone)
    return moment


def _raw_line(value: Any) -> str:
    if isinstance(value, dict):
        value = {**{key: value[key] for key in _FIELDS_READ if key in value}, **value}
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= MAX_RAW else text[:MAX_RAW] + "…"
