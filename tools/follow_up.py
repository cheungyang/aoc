"""The follow_up tool: an agent's own open loops.

An agent opens one of two kinds of follow-up (core/storage/follow_ups.Kind).

A watch, when your goal isn't met yet and it can keep working on it itself:
checking, searching, re-evaluating. It has a goal, a check, a cadence (`every`,
6 h to 7 d) and an end (`until`, at most MAX_DAYS out:
core/storage/follow_ups.parse_until), and optionally what would end it early
(`close_when`). Its first check, `every` after it opens, must come before
`until` ends (ensure_first_check). The check is a short model turn
({"type": "model", "what": ...}) or one of the agent's own scans, the
deterministic checks in `agents/<agent>/preconditions/`, with its args.

A one-off, when you ask it to bring a topic back to you at a set time ("set it
up so we can talk about this tomorrow"). It has a goal, the time (`at`, on your
wall clock, from MIN_AT_S to MAX_DAYS out: parse_at) and the agent's notes, the
context it will need then. At that time the agent brings the topic up in its
channel, once, and asks whether to get into it now (core/proactive/follow_ups.py).
`at` goes with none of a watch's `every`, `until`, `check` or `close_when`, and
`notes` only with `at`. A plain alarm ("call mom at 5") isn't what it's for:
those stay in your task system.

Every op is the calling agent's own (try_context): `open` stores one for it,
with the channel and session it was opened in (core/storage/follow_ups.py);
`extend` moves a watch's `until` later, keeping its history (a one-off isn't
extended: it's closed, and another opened at the new time); `close` ends one of
its open ones; `list` shows them. A second open watch with the same check, or a
second one-off with the same goal at the same time, is refused. `open`'s reply
tells the agent to say what it will do, and when: that it will keep watching,
and until when, or what it will bring up, on which day and at what time.
`extend`'s tells it to say it's extended.

`open` and `extend` work only in a conversation, once you've agreed: on the
scheduled surface (a scheduled run, a proactive delivery, a follow-up's own
check) the agent can offer them in its message, but not do them. A watch's
last check asks whether to keep watching; you answer in the conversation,
where the agent can extend it. `close` and `list` work everywhere, so a check
can close its own follow-up when its goal is met.

The tools loader gives the tool to every run that keeps memory, with the switch
on (core/loaders/tools_loader.gets_follow_up), but not to a run another agent
asked for (agent_call): a follow-up belongs to the agent you're talking to, and
its checks use that agent's own tools. With the switch off every op is refused:
nothing would check the follow-ups. The checks run on the schedule runner
(core/proactive/follow_ups.py).
"""
import json
import re
import time
from datetime import date, datetime
from typing import Any, Dict, Literal, Optional

from langchain_core.tools import tool

from core.config.settings import Config
from core.runtime.foundation.context import try_context
from core.scheduler.preconditions import PreconditionError, agent_preconditions, build_precondition
from core.storage.follow_ups import (
    MAX_DAYS,
    MODEL_CHECK,
    Check,
    DuplicateFollowUp,
    FollowUp,
    FollowUpStore,
    Status,
    cadence_text,
    ensure_first_check,
    parse_until,
)
from core.tooling import format_tool_response
from core.util.local_time import get_local_timezone, localize, parse_local

NAME = "follow_up"
GOAL_CHARS = 300
# A one-off's notes: the context the agent will need when it brings the topic up.
NOTES_CHARS = 1000
MIN_EVERY_S, MAX_EVERY_S = 6 * 3600, 7 * 86400
# The soonest a one-off can be: sooner, the agent can say it now, and a tick and a turn
# would bring it up late anyway.
MIN_AT_S = 5 * 60
MODEL_FORM = '{"type": "model", "what": "<what to look for>"}'
AT_FORM = '"YYYY-MM-DD HH:MM"'
# Where `open` and `extend` are refused: posts nobody asked for (ExecutionContext.get_surface).
SCHEDULED = "scheduled"
NOT_HERE = "open or extend a follow-up in a conversation, after the user agreed; you can offer it in your message"

_EVERY = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([mhd])\s*$", re.IGNORECASE)
_UNIT_S = {"m": 60, "h": 3600, "d": 86400}


def parse_every(text: str) -> int:
    """`every` in seconds: "12h", "2d", "90m", from 6 h to 7 d. Raises ValueError."""
    match = _EVERY.match(text or "")
    if not match:
        raise ValueError('every is like "12h" or "2d" (or "90m")')
    seconds = int(float(match.group(1)) * _UNIT_S[match.group(2).lower()])
    if not MIN_EVERY_S <= seconds <= MAX_EVERY_S:
        raise ValueError(f"every is from 6h to 7d, not {text.strip()}")
    return seconds


def now() -> float:
    """The moment `open` and `extend` count from, epoch seconds: read in one place."""
    return time.time()


def today() -> date:
    """Today on your wall clock, not the container's: where `until` counts from."""
    return datetime.fromtimestamp(now(), get_local_timezone()).date()


def local(at: float) -> datetime:
    """`at` on your wall clock, not the container's."""
    return datetime.fromtimestamp(at, get_local_timezone())


def parse_at(text: str, start: float) -> float:
    """A one-off's `at` in epoch seconds: a time on your wall clock, "YYYY-MM-DD HH:MM" (or
    with a T), from MIN_AT_S to MAX_DAYS after `start`. A time the clocks skip is refused,
    and one they repeat is the first (core/util/local_time.localize). Raises ValueError."""
    try:
        wall = parse_local(text)
    except ValueError:
        wall = None
    if not isinstance(wall, datetime):  # a date alone has no time to bring it up at
        raise ValueError(f"at is a time on the user's clock, {AT_FORM}")
    due = localize(wall, get_local_timezone())[0].timestamp()
    if due <= start:
        raise ValueError(f"at {local(due):%Y-%m-%d %H:%M} has passed: it's {local(start):%Y-%m-%d %H:%M}")
    if due - start < MIN_AT_S:
        raise ValueError(f"at is at least {MIN_AT_S // 60} minutes out: it's {local(start):%Y-%m-%d %H:%M}")
    latest = start + MAX_DAYS * 86400
    if due > latest:
        raise ValueError(f"at is at most {MAX_DAYS} days out: {local(latest):%Y-%m-%d %H:%M} or sooner")
    return due


def parse_check(text: str, agent_id: str) -> Check:
    """The check: a model check, or one of `agent_id`'s scans with its args (written
    {"type": <scan>, ...args}, or in the schedule form, {"type": ..., "args": {...}}).
    Any other type raises ValueError naming the ones allowed."""
    allowed = [MODEL_CHECK] + sorted(agent_preconditions(agent_id))
    try:
        check = json.loads(text) if (text or "").strip() else None
    except ValueError:
        check = None
    if not isinstance(check, dict) or not check.get("type"):
        raise ValueError(f"check is a JSON object with a type ({', '.join(allowed)}), e.g. {MODEL_FORM}")
    kind = str(check["type"])
    if kind == MODEL_CHECK:
        what = check.get("what")
        if set(check) != {"type", "what"} or not isinstance(what, str) or not what.strip():
            raise ValueError(f"a model check is {MODEL_FORM}")
        what = " ".join(what.split())
        if len(what) > GOAL_CHARS:
            raise ValueError(f"a model check's what is at most {GOAL_CHARS} characters")
        return Check(type=MODEL_CHECK, what=what)
    if kind not in allowed:
        raise ValueError(f"check type '{kind}' isn't one you can use: {', '.join(allowed)}")
    args = {k: v for k, v in check.items() if k != "type"}
    if set(args) == {"args"} and isinstance(args["args"], dict):
        args = dict(args["args"])
    try:
        build_precondition({"type": kind, "args": args}, agent_id)
    except PreconditionError as e:
        raise ValueError(f"check: {e}") from None
    return Check(type=kind, args=args)


def line(follow_up: FollowUp) -> str:
    """An open follow-up, as `list` shows it: a watch's goal, cadence, end and check, or
    when a one-off is brought up, and what."""
    if follow_up.is_once:
        return f"#{follow_up.id} · once at {local(follow_up.due_at):%a %Y-%m-%d %H:%M} · \"{follow_up.goal}\""
    early = f" · ends early when: {follow_up.close_when}" if follow_up.close_when else ""
    return (f"#{follow_up.id} · \"{follow_up.goal}\" · every {cadence_text(follow_up.interval_s)} "
            f"until {follow_up.until} · check: {follow_up.check.text()}{early}")


def _error(message: str) -> str:
    return format_tool_response(NAME, payload="", errors=f"Error: {message}")


def _open(ctx: Any, goal: str, check: str, every: str, until: str, close_when: str) -> str:
    goal, close_when = " ".join((goal or "").split()), " ".join((close_when or "").split())
    if not goal:
        return _error("open needs a goal: what the user wants")
    if len(goal) > GOAL_CHARS or len(close_when) > GOAL_CHARS:
        return _error(f"goal and close_when are at most {GOAL_CHARS} characters each")
    at = now()
    try:
        parsed = parse_check(check, ctx.agent_id)
        every_s = parse_every(every)
        day = parse_until(until, today())
        ensure_first_check(every_s, day, at)
    except ValueError as e:
        return _error(str(e))
    try:
        opened = FollowUpStore().open(agent_id=ctx.agent_id, goal=goal, check=parsed, every_s=every_s, until=day,
                                      close_when=close_when, channel=ctx.channel_name or "",
                                      session_id=ctx.session_id or "", at=at)
    except DuplicateFollowUp as e:
        found = e.existing
        return _error(f"follow-up #{found.id} already has this check, until {found.until}: "
                      f"\"{found.goal}\". Close it first to change it.")
    return format_tool_response(NAME, payload=(
        f"Opened follow-up #{opened.id}: \"{opened.goal}\", checked every {cadence_text(opened.every_s)} "
        f"until {opened.until}. Say in your reply that you'll keep watching, and until when ({opened.until})."))


def _open_once(ctx: Any, goal: str, at: str, notes: str, watch: Dict[str, str]) -> str:
    """`open` with `at`: a one-off. `watch` holds the watch's own args, by name: any given
    is refused, since a one-off has no check, cadence or end of its own."""
    given = [name for name, value in watch.items() if (value or "").strip()]
    if given:
        return _error(f"at can't go with {' or '.join(given)}: a one-off is brought up once, at its time")
    goal, notes = " ".join((goal or "").split()), " ".join((notes or "").split())
    if not goal:
        return _error("open needs a goal: what to bring up")
    if len(goal) > GOAL_CHARS:
        return _error(f"goal is at most {GOAL_CHARS} characters")
    if len(notes) > NOTES_CHARS:
        return _error(f"notes are at most {NOTES_CHARS} characters")
    opened_at = now()
    try:
        due_at = parse_at(at, opened_at)
    except ValueError as e:
        return _error(str(e))
    try:
        opened = FollowUpStore().open_once(agent_id=ctx.agent_id, goal=goal, due_at=due_at, notes=notes,
                                           channel=ctx.channel_name or "", session_id=ctx.session_id or "",
                                           at=opened_at)
    except DuplicateFollowUp as e:
        return _error(f"follow-up #{e.existing.id} already brings this up then: \"{e.existing.goal}\"")
    when = f"{local(opened.due_at):%A %Y-%m-%d %H:%M}"
    return format_tool_response(NAME, payload=(
        f"Opened follow-up #{opened.id}: you'll bring up \"{opened.goal}\" on {when}. Say in your reply what "
        f"you'll bring up, and when ({when})."))


def _extend(ctx: Any, follow_up_id: Optional[int], until: str) -> str:
    if follow_up_id is None:
        return _error("extend needs the follow-up's id (list shows them)")
    store = FollowUpStore()
    current = store.get(int(follow_up_id))
    if current is None or not current.is_open or current.agent_id != ctx.agent_id:
        return _error(f"you have no open follow-up #{follow_up_id}")
    if current.is_once:  # whatever `until` says: a later one would only stretch its grace day
        return _error(f"follow-up #{current.id} is a one-off, brought up once: it can't be extended. Close it, "
                      "and open another at the new time")
    try:
        day = parse_until(until, today())
    except ValueError as e:
        return _error(str(e))
    extended = store.extend(current.id, day, agent_id=ctx.agent_id)
    if extended is not None:
        return format_tool_response(NAME, payload=f"Extended follow-up #{extended.id} until {extended.until}. "
                                                  "Say so in your reply.")
    current = store.get(current.id)  # it may have closed since
    if current is None or not current.is_open:
        return _error(f"you have no open follow-up #{follow_up_id}")
    return _error(f"until is after {current.until}: follow-up #{current.id} already runs until then")


@tool
def follow_up(op: Literal["open", "extend", "close", "list"], goal: str = "", check: str = "", every: str = "",
              until: str = "", close_when: str = "", at: str = "", notes: str = "", id: Optional[int] = None,
              reason: str = "") -> str:
    """A watch: a check you run with your own tools on a cadence until an end date. A one-off
    (at): a topic the user asks you to bring back at a set time. Offer first, say in your
    reply what you'll do and when, and close it when done or the user says stop.

    Args:
        op: open, extend, close, or list yours.
        goal, check, every, until, close_when: a watch. check is JSON:
            {"type": "model", "what": "..."} or one of your scans with its args.
            every: 6h to 7d. until: YYYY-MM-DD, within 90 days.
        at, goal, notes: a one-off. at: YYYY-MM-DD HH:MM.
        id, until: extend a watch. id, reason: close.
    """
    if not Config().proactive_delivery:
        return _error("follow-ups are off: nothing would check them")
    ctx = try_context()
    if ctx is None:
        return _error("no active execution context; this tool must be called from an agent run")
    if op in ("open", "extend") and ctx.get_surface() == SCHEDULED:
        return _error(NOT_HERE)
    if op == "open":
        if (at or "").strip():
            return _open_once(ctx, goal, at, notes, {"every": every, "until": until, "check": check,
                                                     "close_when": close_when})
        if (notes or "").strip():
            return _error(f"notes go with at, for a one-off: at is {AT_FORM}")
        return _open(ctx, goal, check, every, until, close_when)
    if op == "extend":
        return _extend(ctx, id, until)
    if op == "close":
        if id is None:
            return _error("close needs the follow-up's id (list shows them)")
        closed = FollowUpStore().close(int(id), agent_id=ctx.agent_id, reason=" ".join((reason or "").split()))
        if closed is None:
            return _error(f"you have no open follow-up #{id}")
        return format_tool_response(NAME, payload=f"Closed follow-up #{closed.id}: \"{closed.goal}\".")
    if op == "list":
        follow_ups = FollowUpStore().list(agent_id=ctx.agent_id, status=Status.OPEN)
        return format_tool_response(NAME, payload="\n".join(map(line, follow_ups)) or "You have no open follow-ups.")
    return _error(f"op is open, extend, close or list, not {op!r}")
