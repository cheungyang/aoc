"""Local wall-clock time and UTC, DST-safe.

The model speaks local time ("Saturday, 9 to 1"); Google Calendar wants an
instant. Converting by hand is where daylight saving bites: in Los Angeles,
Sun Nov 1 2026 has 25 hours and 01:30 happens twice, and Sun Mar 14 2027 has
23 hours and 02:30 never happens. zoneinfo knows the rules; these helpers ask it.
"""
import re
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Optional, Tuple, Union

_LOCAL_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?$")


class LocalTimeError(ValueError):
    """A time that names no single instant: unparseable, or skipped by a clock change."""


def zone_name(tz: tzinfo) -> str:
    return getattr(tz, "key", None) or str(tz)


def day_label(day: date) -> str:
    """'Sat Oct 24'."""
    return f"{day:%a %b} {day.day}"


def utc_text(moment: datetime) -> str:
    """'2026-10-24T16:00:00Z'."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_local(value: str) -> Union[date, datetime]:
    """'2026-10-24' -> a date; '2026-10-24 09:00' or '2026-10-24T9:00:00' -> a naive
    datetime (local wall time). A time with an offset ('...-07:00', '...Z') comes
    back aware, so an exact instant is kept as given."""
    text = str(value or "").strip()
    if not text:
        raise LocalTimeError("a time is required, like '2026-10-24 09:00'")
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    match = _LOCAL_TIME.match(text)
    if match:
        day, hour, minute, second = match.groups()
        text = f"{day}T{int(hour):02d}:{minute}:{second or '00'}"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        raise LocalTimeError(
            f"{value!r} is not a time like '2026-10-24 09:00' or a date like '2026-10-24'"
        ) from None


def localize(wall: datetime, tz: tzinfo) -> Tuple[datetime, Optional[str]]:
    """The instant a local wall time names in `tz`, and a note when DST makes it odd.

    A wall time skipped when the clocks spring forward raises LocalTimeError. One
    that happens twice when they fall back resolves to the first (still daylight
    time), and the note says so. An aware datetime is already an instant.
    """
    if wall.tzinfo is not None:
        return wall.astimezone(tz), None
    first = wall.replace(tzinfo=tz, fold=0)
    if first.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) != wall:
        raise LocalTimeError(
            f"{day_label(wall.date())} {wall:%H:%M} doesn't exist in {zone_name(tz)}: "
            "the clocks spring forward an hour then. Pick another time."
        )
    if first.utcoffset() != wall.replace(tzinfo=tz, fold=1).utcoffset():
        return first, (
            f"{wall:%H:%M} happens twice on {day_label(wall.date())} (the clocks fall back); "
            f"used the first, {first:%Z}."
        )
    return first, None


def describe_span(start: datetime, end: datetime, tz: tzinfo) -> str:
    """'Sat Oct 24, 09:00–13:00 PDT'; both ends in full when the span crosses
    midnight or a clock change."""
    s, e = start.astimezone(tz), end.astimezone(tz)
    if s.date() == e.date() and s.utcoffset() == e.utcoffset():
        return f"{day_label(s.date())}, {s:%H:%M}–{e:%H:%M} {s:%Z}"
    return f"{day_label(s.date())} {s:%H:%M} {s:%Z} – {day_label(e.date())} {e:%H:%M} {e:%Z}"


def describe_days(first: date, last: date) -> str:
    """'Sat Oct 24 (all day)' or 'Sat Oct 24 – Sun Oct 25 (all day)'; `last` is inclusive."""
    if first == last:
        return f"{day_label(first)} (all day)"
    return f"{day_label(first)} – {day_label(last)} (all day)"


def clock_change(start: datetime, end: datetime, tz: tzinfo) -> Optional[str]:
    """The DST change between two instants, e.g. 'Sun Nov 1: clocks fall back 1 h at
    02:00 (PDT → PST)', or None. Assumes at most one change, which holds for any
    span of a few days."""
    lo, hi = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    before = lo.astimezone(tz).utcoffset()
    if hi.astimezone(tz).utcoffset() == before:
        return None
    old_abbr = lo.astimezone(tz).strftime("%Z")
    while hi - lo > timedelta(minutes=1):
        mid = lo + (hi - lo) / 2
        if mid.astimezone(tz).utcoffset() == before:
            lo = mid
        else:
            hi = mid
    # Transitions fall on whole minutes; the one in (lo, hi] is the change.
    change = hi.replace(second=0, microsecond=0)
    if change <= lo:
        change += timedelta(minutes=1)
    after = change.astimezone(tz)
    shift = after.utcoffset() - before
    minutes = int(abs(shift).total_seconds() // 60)
    amount = f"{minutes // 60} h" if minutes % 60 == 0 else f"{minutes} min"
    old_wall = change.astimezone(timezone(before))
    direction = "spring forward" if shift > timedelta(0) else "fall back"
    return (
        f"{day_label(old_wall.date())}: clocks {direction} {amount} at {old_wall:%H:%M} "
        f"({old_abbr} → {after:%Z})"
    )
