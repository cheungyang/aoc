"""Timezone-aware clock helpers.

The process normally runs inside the Docker container, whose system clock is
UTC. Anything that called `datetime.now()` therefore spoke UTC while the human
reading the output lives in PST/PDT -- visible as the agent claiming "today" was
already tomorrow for the whole evening, and as cron schedules firing 7-8 hours
early. These helpers resolve the user's zone from config so callers get wall
clock time the user recognises regardless of where the process runs.
"""

import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def get_local_timezone() -> datetime.tzinfo:
    """Timezone every user-facing date/time should be expressed in."""
    tz_name = None
    try:
        from core.util.config import Config
        tz_name = Config().timezone
    except Exception:
        pass

    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            pass

    # Last resort: the host's own zone, resolved to a fixed offset so callers
    # still get an aware datetime rather than a silently naive one.
    return datetime.datetime.now().astimezone().tzinfo or datetime.timezone.utc


def get_local_now() -> datetime.datetime:
    """Current time as an aware datetime in the user's timezone."""
    return datetime.datetime.now(get_local_timezone())


def format_timezone_label(now: datetime.datetime = None) -> str:
    """Human/LLM readable zone label, e.g. 'America/Los_Angeles (PDT, UTC-07:00)'.

    Includes the numeric offset because that, not the abbreviation, is what
    lets a reader convert a UTC timestamp without knowing the DST rules.
    """
    if now is None:
        now = get_local_now()

    abbr = now.strftime("%Z") or "UTC"
    offset = now.strftime("%z") or "+0000"
    offset_label = f"UTC{offset[:3]}:{offset[3:]}"
    name = str(getattr(now.tzinfo, "key", "") or abbr)
    return f"{name} ({abbr}, {offset_label})"
