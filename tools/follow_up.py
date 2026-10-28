"""The follow_up tool: an agent's own open loops.

An agent opens a follow-up when your goal isn't met yet and it can keep
working on it itself: checking, searching, re-evaluating. Never a reminder for
you; those stay in your task system. A follow-up has a goal, a check, a cadence
(`every`, 6 h to 7 d) and an end (`until`, at most MAX_DAYS out), and optionally
what would end it early (`close_when`). The check is a short model turn
({"type": "model", "what": ...}) or one of the agent's own scans, the
deterministic checks in `agents/<agent>/preconditions/`, with its args.

Every op is the calling agent's own (try_context): `open` stores one for it,
with the channel and session it was opened in (core/storage/follow_ups.py);
`close` ends one of its open ones; `list` shows them. A second open follow-up
with the same check is refused. `open`'s reply tells the agent to say it will
keep watching, and until when.

`open` works only in a conversation, once you've agreed: on the scheduled
surface (a scheduled run, a proactive delivery, a follow-up's own check) the
agent can offer one in its message, but not open it. `close` and `list` work
everywhere, so a check can close its own follow-up when its goal is met.

The tools loader gives the tool to every run that keeps memory, with the switch
on (core/loaders/tools_loader.gets_follow_up). With the switch off every op is
refused: nothing would check the follow-ups. The checks run on the schedule
runner (core/proactive/follow_ups.py).
"""
import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Literal, Optional

from langchain_core.tools import tool

from core.config.settings import Config
from core.runtime.foundation.context import try_context
from core.scheduler.preconditions import PreconditionError, agent_preconditions, build_precondition
from core.storage.follow_ups import MODEL_CHECK, Check, DuplicateFollowUp, FollowUp, FollowUpStore, Status, cadence_text
from core.tooling import format_tool_response
from core.util.local_time import get_local_timezone

NAME = "follow_up"
GOAL_CHARS = 300
MIN_EVERY_S, MAX_EVERY_S = 6 * 3600, 7 * 86400
MAX_DAYS = 90
MODEL_FORM = '{"type": "model", "what": "<what to look for>"}'
# Where `open` is refused: posts nobody asked for (ExecutionContext.get_surface).
SCHEDULED = "scheduled"
NOT_HERE = "open a follow-up in a conversation, after the user agreed; you can offer it in your message"

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


def parse_until(text: str, today: date) -> str:
    """`until` as YYYY-MM-DD: from `today` to MAX_DAYS after it. Raises ValueError."""
    try:
        day = date.fromisoformat((text or "").strip())
    except ValueError:
        raise ValueError("until is a date, YYYY-MM-DD") from None
    latest = today + timedelta(days=MAX_DAYS)
    if day < today:
        raise ValueError(f"until {day} has passed: it's {today} today")
    if day > latest:
        raise ValueError(f"until is at most {MAX_DAYS} days out: {latest} or sooner")
    return day.isoformat()


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
    """An open follow-up, as `list` shows it."""
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
    try:
        parsed = parse_check(check, ctx.agent_id)
        every_s = parse_every(every)
        day = parse_until(until, datetime.now(get_local_timezone()).date())
    except ValueError as e:
        return _error(str(e))
    try:
        opened = FollowUpStore().open(agent_id=ctx.agent_id, goal=goal, check=parsed, every_s=every_s, until=day,
                                      close_when=close_when, channel=ctx.channel_name or "",
                                      session_id=ctx.session_id or "")
    except DuplicateFollowUp as e:
        found = e.existing
        return _error(f"follow-up #{found.id} already has this check, until {found.until}: "
                      f"\"{found.goal}\". Close it first to change it.")
    return format_tool_response(NAME, payload=(
        f"Opened follow-up #{opened.id}: \"{opened.goal}\", checked every {cadence_text(opened.every_s)} "
        f"until {opened.until}. Say in your reply that you'll keep watching, and until when ({opened.until})."))


@tool
def follow_up(op: Literal["open", "close", "list"], goal: str = "", check: str = "", every: str = "",
              until: str = "", close_when: str = "", id: Optional[int] = None, reason: str = "") -> str:
    """Keep working on the user's unmet goal: a check you run on a cadence until an end date.
    Only for work you can do (checking, searching, re-evaluating), never reminders. Offer
    first, say in your reply what you'll watch until when, and close it when the goal is met
    or the user says stop.

    Args:
        op: open, close, or list yours.
        goal, check, every, until, close_when: for open. check is JSON:
            {"type": "model", "what": "..."} or one of your scans with its args.
            every: 6h to 7d. until: YYYY-MM-DD, within 90 days. close_when: optional.
        id, reason: for close.
    """
    if not Config().proactive_delivery:
        return _error("follow-ups are off: nothing would check them")
    ctx = try_context()
    if ctx is None:
        return _error("no active execution context; this tool must be called from an agent run")
    if op == "open":
        if ctx.get_surface() == SCHEDULED:
            return _error(NOT_HERE)
        return _open(ctx, goal, check, every, until, close_when)
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
    return _error(f"op is open, close or list, not {op!r}")
