"""The next weekends from the calendar: dates, what's booked, and the free windows.

Planning a weekend needs three facts the model used to work out itself: which
dates "the next 3 weekends" are, what's already on them, and where the gaps
are. Output is local wall clock; the arithmetic runs on UTC instants, so a day
with a clock change (Sun Nov 1 2026 has 25 hours) still adds up.

When free time counts (the hours, the shortest gap) is the user's call: RULES.md,
read into a `FreeTime` by `free_time`.
"""
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

from core.tooling import Report
from core.util.local_time import clock_change, day_label, zone_name
from agents.excursion_planner.lib.events import CalendarEvent
from agents.excursion_planner.lib.rules import RULES

MAX_WEEKS = 8
_HALF_HOUR = 30 * 60
_DEFAULTS = {rule.key: rule.default for rule in RULES}
_CLOCK = re.compile(r"^(\d{1,2})(?::(\d{2}))?$")

Interval = Tuple[datetime, datetime]


@dataclass(frozen=True)
class FreeTime:
    """When free time counts: from `start` to `end`, local wall clock, in gaps of at least `min_gap`."""
    start: time
    end: time
    min_gap: timedelta

    def describe(self) -> str:
        """'08:00–22:00, gaps of 1 h or more'."""
        return f"{self.start:%H:%M}–{self.end:%H:%M}, gaps of {_hours(self.min_gap)} h or more"


def free_time(rules: Mapping[str, Any], report: Report, source: str = "RULES.md") -> FreeTime:
    """`free_from`, `free_until` and `min_gap_hours` from `rules` (load_rules' values) as a FreeTime.

    A value that doesn't read as a time, hours that don't make a window, or a
    gap outside 0–24 h is an issue in `report` (named after `source`), and the
    default applies. A key `rules` leaves out takes its default.
    """
    start = _clock(rules, "free_from", report, source)
    end = _clock(rules, "free_until", report, source)
    if start >= end:
        report.problem(source, f"free_from {start:%H:%M} isn't before free_until {end:%H:%M}; "
                               f"using {_DEFAULTS['free_from']}–{_DEFAULTS['free_until']}")
        start, end = _parse_clock(_DEFAULTS["free_from"]), _parse_clock(_DEFAULTS["free_until"])
    hours = rules.get("min_gap_hours", _DEFAULTS["min_gap_hours"])
    if not 0 <= hours <= 24:
        report.problem(source, f"min_gap_hours: {hours:g} isn't between 0 and 24; "
                               f"using {_DEFAULTS['min_gap_hours']:g}")
        hours = _DEFAULTS["min_gap_hours"]
    return FreeTime(start, end, timedelta(hours=hours))


def upcoming_saturdays(today: date, weeks: int) -> List[date]:
    """The Saturdays of the next `weeks` weekends. This weekend counts until its
    Saturday is over: on a Saturday it comes first, on a Sunday next weekend does."""
    first = today + timedelta(days=(5 - today.weekday()) % 7)
    return [first + timedelta(weeks=i) for i in range(weeks)]


def query_range(today: date, weeks: int, tz: tzinfo) -> Interval:
    """The instants to fetch events for: the first Saturday 00:00 to the Monday 00:00
    after the last weekend, local."""
    saturdays = upcoming_saturdays(today, weeks)
    return _midnight(saturdays[0], tz), _midnight(saturdays[-1] + timedelta(days=2), tz)


def gaps(busy: Iterable[Interval], window_start: datetime, window_end: datetime) -> List[Interval]:
    """Every gap between busy intervals, inside the window."""
    cursor, found = window_start, []
    for start, end in sorted(busy):
        if start >= window_end:
            break
        if end <= cursor:
            continue
        if start > cursor:
            found.append((cursor, start))
        cursor = end
    if cursor < window_end:
        found.append((cursor, window_end))
    return found


def free_windows(busy: Iterable[Interval], window_start: datetime, window_end: datetime,
                 min_gap: timedelta) -> List[Interval]:
    """The gaps of at least `min_gap` between busy intervals, inside the window."""
    return [(start, end) for start, end in gaps(busy, window_start, window_end) if end - start >= min_gap]


def weekend_availability(
    events: Sequence[CalendarEvent], now: datetime, weeks: int, free: FreeTime, report: Report
) -> dict:
    """Each of the next `weeks` weekends: its days, their events, and their free windows.

    `now` is aware and local; today's free time starts from it. Each day's
    arithmetic goes in `report`, for explain mode.
    """
    tz = now.tzinfo
    this_saturday = now.date() + timedelta(days=5 - now.date().weekday())
    weekends = []
    for saturday in upcoming_saturdays(now.date(), weeks):
        sunday = saturday + timedelta(days=1)
        weekend = {
            "weekend": _weekend_label(saturday, sunday),
            "when": _relative(saturday, this_saturday),
            "days": [day_availability(day, events, now, free, report) for day in (saturday, sunday)],
        }
        change = clock_change(_midnight(saturday, tz), _midnight(saturday + timedelta(days=2), tz), tz)
        if change:
            weekend["note"] = change
        weekends.append(weekend)
    return {"timezone": zone_name(tz), "free_time": free.describe(), "weekends": weekends}


def day_availability(day: date, events: Sequence[CalendarEvent], now: datetime, free: FreeTime,
                     report: Report) -> dict:
    """One day: its timed events, its all-day events, its free windows and free hours."""
    tz = now.tzinfo
    day_start, day_end = _midnight(day, tz), _midnight(day + timedelta(days=1), tz)
    window_start = _at(day, free.start, tz)
    if day == now.date():
        window_start = max(window_start, _round_up(now))
    window_end = _at(day, free.end, tz)

    timed = sorted(
        (e for e in events if not e.all_day and e.start < day_end and e.end > day_start),
        key=lambda e: e.start,
    )
    all_day = [e for e in events if e.all_day and e.first_day <= day < e.end_day]
    busy = [(e.start, e.end) for e in timed if e.busy]
    if any(e.busy for e in all_day):
        busy.append((day_start, day_end))
    windows = free_windows(busy, window_start, window_end, free.min_gap) if window_start < window_end else []

    entry = {"date": day.isoformat(), "day": day_label(day)}
    if timed:
        entry["events"] = [_timed_label(e, day_start, day_end, tz) for e in timed]
    if all_day:
        entry["all_day"] = [f"{e.title} (busy)" if e.busy else e.title for e in all_day]
    entry["free"] = [f"{_local(s, tz)}–{_local(e, tz)}" for s, e in windows]
    entry["free_hours"] = round(sum((e - s for s, e in windows), timedelta()).total_seconds() / 3600, 1)
    report.trace(_day_trace(day, entry["free_hours"], windows, busy, all_day, window_start, window_end, now, free))
    return entry


def _day_trace(day: date, total: float, windows: List[Interval], busy: List[Interval],
               all_day: Sequence[CalendarEvent], window_start: datetime, window_end: datetime,
               now: datetime, free: FreeTime) -> str:
    """'Sat Oct 24: 12 h free = 08:00–09:00 (1 h) + 11:00–22:00 (11 h), counting 08:00–22:00'."""
    tz = now.tzinfo
    blocking = [e.title for e in all_day if e.busy]
    if blocking:
        return f"{day_label(day)}: 0 h free, busy all day ({', '.join(blocking)})"
    if window_start >= window_end:
        return f"{day_label(day)}: 0 h free, nothing left of {free.start:%H:%M}–{free.end:%H:%M} after now"
    parts = " + ".join(f"{_local(s, tz)}–{_local(e, tz)} ({_hours(e - s)} h)" for s, e in windows) or "no gap"
    counting = f"{_local(window_start, tz)}–{_local(window_end, tz)}" + (" from now" if day == now.date() else "")
    line = f"{day_label(day)}: {total:g} h free = {parts}, counting {counting}"
    short = [g for g in gaps(busy, window_start, window_end) if g not in windows]
    if short:
        spans = ", ".join(f"{_local(s, tz)}–{_local(e, tz)}" for s, e in short)
        line += f"; under {_hours(free.min_gap)} h, so left out: {spans}"
    return line


def _clock(rules: Mapping[str, Any], key: str, report: Report, source: str) -> time:
    """`rules[key]` as a time of day; the default, and an issue, when it doesn't read."""
    raw = rules.get(key, _DEFAULTS[key])
    parsed = _parse_clock(raw)
    if parsed is None:
        report.problem(source, f"{key}: '{raw}' isn't a time like 08:00; using {_DEFAULTS[key]}")
        return _parse_clock(_DEFAULTS[key])
    return parsed


def _parse_clock(raw: Any) -> Optional[time]:
    """'08:00', '8:30' or '8' (24-hour) as a time; None when it isn't one."""
    match = _CLOCK.match(str(raw).strip())
    if match:
        hour, minute = int(match.group(1)), int(match.group(2) or 0)
        if hour < 24 and minute < 60:
            return time(hour, minute)
    return None


def _timed_label(event: CalendarEvent, day_start: datetime, day_end: datetime, tz: tzinfo) -> str:
    """'09:00–11:00 Soccer'; '…' marks an end that falls on another day."""
    start = _local(event.start, tz) if event.start >= day_start else "…"
    end = _local(event.end, tz) if event.end <= day_end else "…"
    label = f"{start}–{end} {event.title}"
    return label if event.busy else f"{label} (free)"


def _relative(saturday: date, this_saturday: date) -> str:
    """By calendar week (Mon–Sun): 'this weekend', 'next weekend', 'in 3 weeks'."""
    weeks = (saturday - this_saturday).days // 7
    if weeks <= 0:
        return "this weekend"
    return "next weekend" if weeks == 1 else f"in {weeks} weeks"


def _weekend_label(saturday: date, sunday: date) -> str:
    """'Oct 17–18', or 'Oct 31–Nov 1' across a month end."""
    if saturday.month == sunday.month:
        return f"{saturday:%b} {saturday.day}–{sunday.day}"
    return f"{saturday:%b} {saturday.day}–{sunday:%b} {sunday.day}"


def _midnight(day: date, tz: tzinfo) -> datetime:
    return _at(day, time(0), tz)


def _at(day: date, wall: time, tz: tzinfo) -> datetime:
    return datetime.combine(day, wall, tzinfo=tz).astimezone(timezone.utc)


def _local(moment: datetime, tz: tzinfo) -> str:
    return f"{moment.astimezone(tz):%H:%M}"


def _round_up(moment: datetime) -> datetime:
    """To the next half hour, so 'free from now' reads as a time you can plan for."""
    stamp = math.ceil(moment.timestamp() / _HALF_HOUR) * _HALF_HOUR
    return datetime.fromtimestamp(stamp, timezone.utc)


def _hours(span: timedelta) -> str:
    hours = span.total_seconds() / 3600
    return f"{hours:g}"
