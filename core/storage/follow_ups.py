"""Follow-ups in memory.db: each agent's own open loops, and what a follow-up is.

A follow-up is a goal, a check, a cadence and an end, for work the agent can do
itself: checking, searching, re-evaluating. It exists only while there's a
need. The follow_up tool (tools/follow_up.py) opens one for the calling agent,
extends it, closes it, and lists the open ones; `!followups` lists them all,
and extends or cancels one (core/runtime/commands/typed/followups.py). The
Switchboard closes one when your own words to another teammate end it, in its
channel or with "@Name …" (core/proactive/pipeline.py).

Its end, `until`, is its last day on your wall clock: from today to MAX_DAYS
out (`parse_until`, which the tool and `!followups` share). It ends at the
midnight after (`end_of`), and its first check must come before then
(`ensure_first_check`): one that never checks would never offer to keep
watching. `extend` moves it later and keeps the history: the counts, the
fingerprint and what the agent last told you. Closing and opening again would
lose them, and old findings would look new.

Every read returns a FollowUp: every column but `check_key`, with its check a
Check and each fixed vocabulary an enum (Status, Closer). A check is stored as
JSON, {"type": "model", "what": ...} or a scan's {"type": <scan>, ...args}:
`Check.from_spec` reads that form and `Check.spec` writes it.

Two follow-ups are the same check when their `check_key`s match: the agent and
its check, normalised (`check_key`). While one is open, opening the same check
again raises DuplicateFollowUp, naming the open one; the partial unique index in
the schema (core/storage/schemas/follow_ups.sql) makes that atomic.

The checks run on the schedule runner (core/proactive/follow_ups.py):
  - `due` lists the checks due now, `claim` takes one for one run, and
    `release_stale` frees a claim whose run never finished;
  - `finish_check` counts how a check went (a Result) and sets when the next
    one is due. A find puts the interval back to `every`; every EMPTY_STREAK-th
    empty check in a row doubles it, up to MAX_INTERVAL_S; and when less than
    two intervals are left before `until` ends, it's back to `every`, so the
    last days are watched as closely as the first;
  - `expire` closes the ones whose `until` has passed.
Team radio announces each one opened and each one closed, once (`pending_lines`,
`mark_line`). `cadence_text`, `Check.text`, `FollowUp.tally` and
`FollowUp.closed_why` word a follow-up the same way for the tool, `!followups`
and team radio.
"""
import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as clock, timedelta
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional

from core.config.settings import Config
from core.storage.base import Row, Store
from core.util.local_time import get_local_timezone

UNTIL_PASSED = "until passed"
# The furthest `until` can be, in days from today: a follow-up has an end in sight.
MAX_DAYS = 90
# The check that is a short model turn. Any other type is one of the agent's scans.
MODEL_CHECK = "model"
# Empty checks in a row that double the interval (the 3rd, the 6th, ...), and the
# longest it gets: `every`'s own upper bound.
EMPTY_STREAK = 3
MAX_INTERVAL_S = 7 * 86400


class Status(str, Enum):
    """Whether a follow-up still runs; also the last team radio line it had (`radio_state`)."""
    OPEN = "open"
    CLOSED = "closed"


class Closer(str, Enum):
    """Who closed a follow-up (`closed_by`). The tool closes as the agent."""
    AGENT = "agent"              # its agent
    YOU = "you"                  # you: `!followups cancel`
    UNTIL = "until"              # its `until` passing (`expire`)
    SWITCHBOARD = "switchboard"  # the Switchboard, on your own words that end it (core/proactive/pipeline.py)


class Result(str, Enum):
    """How a check went (`finish_check`)."""
    FOUND = "found"      # it posted something new
    EMPTY = "empty"      # it found nothing new, or its agent judged it not worth a message
    PROBLEM = "problem"  # its scan couldn't read what it needed
    FAILED = "failed"    # its turn failed


@dataclass(frozen=True)
class Check:
    """What a follow-up's check runs: a short model turn, or one of its agent's scans
    (`agents/<agent>/preconditions/`) with its args."""
    type: str       # MODEL_CHECK, or the scan's type; "" when the stored check can't be read
    what: str = ""  # a model check's question; "" for a scan
    # A scan's args; {} for a model check. Left out of the hash: a dict has none.
    args: Dict[str, Any] = field(default_factory=dict, hash=False)

    @classmethod
    def from_spec(cls, spec: Mapping[str, Any]) -> "Check":
        """The check stored as `spec`: {"type": "model", "what": ...}, or {"type": <scan>,
        ...args}. One with no type is Check(type="")."""
        kind = str(spec.get("type") or "")
        args = {key: value for key, value in spec.items() if key != "type"}
        if kind == MODEL_CHECK:
            return cls(type=kind, what=str(args.get("what") or ""))
        return cls(type=kind, args=args)

    def spec(self) -> Dict[str, Any]:
        """The stored JSON form, as `from_spec` reads it."""
        if self.is_model:
            return {"type": self.type, "what": self.what}
        return {"type": self.type, **self.args}

    @property
    def is_model(self) -> bool:
        return self.type == MODEL_CHECK

    def text(self) -> str:
        """The check in a few words: `model, "<what>"` for a model check, else its scan's type."""
        if self.is_model:
            return f"model, \"{self.what}\""
        return self.type or "?"


@dataclass(frozen=True)
class FollowUp:
    """One follow-up: its goal, check, cadence and end, and how its checks went. Every
    column of follow_ups.sql but `check_key`, which only the store uses."""
    id: int                                  # its number: "follow-up #12"
    agent_id: str                            # whose loop it is: of the agents, only it closes it
    goal: str                                # what the user wants, in the agent's words
    check: Check                             # what each check runs (`check_spec`)
    every_s: int                             # the cadence the agent asked for, in seconds
    interval_s: int                          # the cadence now: every_s at open, then the checks adapt it
    until: str                               # YYYY-MM-DD, on your wall clock: its last day; `extend` moves it later
    opened_at: float                         # epoch seconds
    next_check_at: float                     # when its check is due: opened_at + every_s at first
    close_when: str = ""                     # what would end it early, in words; "" if nothing
    status: Status = Status.OPEN             # open, then closed for good
    closed_at: Optional[float] = None        # epoch seconds; None while open
    closed_by: Optional[Closer] = None       # who closed it; None while open
    close_reason: str = ""                   # why, in its closer's words; "" if none was given
    channel: str = ""                        # where it was opened, and where what it finds will post
    session_id: str = ""                     # the session it was opened in
    checks: int = 0                          # checks run
    finds: int = 0                           # checks that found something new
    empty_checks: int = 0                    # checks in a row that found nothing new
    last_checked_at: Optional[float] = None  # when the last check ran
    last_found_at: Optional[float] = None    # when a check last found something new
    last_fingerprint: str = ""               # what the last check found, so only something new posts
    last_report: str = ""                    # what the last find posted, clipped
    claimed_at: Optional[float] = None       # when a tick took its due check; None while no check holds it
    radio_state: Optional[Status] = None     # the last team radio line posted for it; None before the first

    @property
    def is_open(self) -> bool:
        return self.status is Status.OPEN

    def tally(self) -> str:
        """What its checks came to: "6 checks, 1 find"."""
        checks, finds = self.checks, self.finds
        return f"{checks} check{'' if checks == 1 else 's'}, {finds} find{'' if finds == 1 else 's'}"

    def closed_why(self, who: str) -> str:
        """Why it closed, in words: its agent's reason, or "<who> closed it"; "you cancelled
        it", with your why; "you said it's done", with the Switchboard's reason, when your own
        words ended it; or "until passed". `who` names its agent."""
        reason = self.close_reason
        if self.closed_by is Closer.YOU:
            return f"you cancelled it: {reason}" if reason else "you cancelled it"
        if self.closed_by is Closer.UNTIL:
            return UNTIL_PASSED
        if self.closed_by is Closer.SWITCHBOARD:
            return f"you said it's done: {reason}" if reason else "you said it's done"
        return reason or f"{who} closed it"


def cadence_text(seconds: int) -> str:
    """12 h as "12h", 2 days as "2d", 90 minutes as "90m"."""
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{seconds // 60}m"


def parse_until(text: str, today: date) -> str:
    """`until` as YYYY-MM-DD: from `today` to MAX_DAYS after it. Raises ValueError, in words
    the tool and `!followups` both show."""
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


def end_of(until: str) -> float:
    """When `until` (YYYY-MM-DD) ends on your wall clock: the midnight after it. The
    near-until rule (`finish_check`) and the last check (core/proactive/follow_ups.py)
    count from it."""
    try:
        day = date.fromisoformat(until) + timedelta(days=1)
    except (TypeError, ValueError):
        return 0.0  # unreadable: treated as ending now
    return datetime.combine(day, clock.min, get_local_timezone()).timestamp()


def ensure_first_check(every_s: int, until: str, now: float) -> None:
    """Raises ValueError unless a follow-up opened at `now` gets its first check, `every_s`
    on, before `until` ends: one due only as it ends, or after, never checks, so it never
    offers to keep watching either. `extend` only moves `until` later, so it can't make
    this worse."""
    if now + every_s >= end_of(until):
        raise ValueError(f"every {cadence_text(every_s)} doesn't fit before {until} ends: check more often, "
                         "or pick a later until")


def _normalised(value: Any) -> Any:
    """`value` with its text lower-cased and its spaces collapsed, at any depth."""
    if isinstance(value, str):
        return " ".join(value.lower().split())
    if isinstance(value, Mapping):
        return {str(k): _normalised(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalised(v) for v in value]
    return value


def check_key(agent_id: str, check: Check) -> str:
    """What makes two checks the same: the agent, and its check's stored form with keys
    sorted and text normalised. "Business award SFO→MAD" and "business  award sfo→mad"
    are one check."""
    return f"{agent_id}:{json.dumps(_normalised(check.spec()), sort_keys=True, ensure_ascii=False)}"


def _follow_up(record: Row) -> FollowUp:
    try:
        spec = json.loads(record["check_spec"] or "{}")
    except (TypeError, ValueError):
        spec = {}
    return FollowUp(
        id=record["id"],
        agent_id=record["agent_id"],
        goal=record["goal"],
        check=Check.from_spec(spec if isinstance(spec, dict) else {}),
        every_s=record["every_s"],
        interval_s=record["interval_s"],
        until=record["until"],
        opened_at=record["opened_at"],
        next_check_at=record["next_check_at"],
        close_when=record["close_when"],
        status=Status(record["status"]),
        closed_at=record["closed_at"],
        closed_by=Closer(record["closed_by"]) if record["closed_by"] else None,
        close_reason=record["close_reason"],
        channel=record["channel"],
        session_id=record["session_id"],
        checks=record["checks"],
        finds=record["finds"],
        empty_checks=record["empty_checks"],
        last_checked_at=record["last_checked_at"],
        last_found_at=record["last_found_at"],
        last_fingerprint=record["last_fingerprint"],
        last_report=record["last_report"],
        claimed_at=record["claimed_at"],
        radio_state=Status(record["radio_state"]) if record["radio_state"] else None,
    )


class DuplicateFollowUp(Exception):
    """An open follow-up already has this check: `existing` is that follow-up."""

    def __init__(self, existing: FollowUp):
        self.existing = existing
        super().__init__(f"follow-up #{existing.id} is already open for this check: \"{existing.goal}\"")


class FollowUpStore(Store):
    schema = ("follow_ups",)

    def default_path(self) -> str:
        return Config().memory_db_path

    def open(self, *, agent_id: str, goal: str, check: Check, every_s: int, until: str,
             close_when: str = "", channel: str = "", session_id: str = "",
             at: Optional[float] = None) -> FollowUp:
        """Opens a follow-up for `agent_id`, and returns it. Its cadence starts at `every_s`
        and its first check is due `every_s` from now. Raises DuplicateFollowUp while an
        open follow-up has the same check (`check_key`)."""
        at = time.time() if at is None else at
        key = check_key(agent_id, check)
        values = {"agent_id": agent_id, "goal": goal, "check_spec": json.dumps(check.spec(), ensure_ascii=False),
                  "check_key": key, "every_s": every_s, "interval_s": every_s, "until": until,
                  "close_when": close_when or "", "status": Status.OPEN, "opened_at": at, "channel": channel or "",
                  "session_id": session_id or "", "next_check_at": at + every_s}
        for _ in range(2):  # the open one may close between the refusal and the read: then try again
            try:
                return self.get(self.insert("follow_ups", values))
            except sqlite3.IntegrityError:
                existing = self.select_one("follow_ups", {"check_key": key, "status": Status.OPEN})
                if existing is not None:
                    raise DuplicateFollowUp(_follow_up(existing)) from None
        raise sqlite3.IntegrityError(f"could not open a follow-up for {key}")

    def close(self, follow_up_id: int, *, agent_id: Optional[str] = None, reason: str = "",
              by: Closer = Closer.AGENT, at: Optional[float] = None) -> Optional[FollowUp]:
        """Closes an open follow-up, with `agent_id` only if it's that agent's, and notes who
        closed it (`by`). Returns it closed, or None if there was no such open follow-up.
        One guarded UPDATE, so of two closes at once only one does."""
        by = Closer(by)  # anything but a Closer raises ValueError
        where: Dict[str, Any] = {"id": follow_up_id, "status": Status.OPEN}
        if agent_id is not None:
            where["agent_id"] = agent_id
        values = {"status": Status.CLOSED, "closed_at": time.time() if at is None else at,
                  "close_reason": reason or "", "closed_by": by}
        if not self.update("follow_ups", values, where):
            return None
        return self.get(follow_up_id)

    def extend(self, follow_up_id: int, until: str, *, agent_id: Optional[str] = None) -> Optional[FollowUp]:
        """Moves an open follow-up's `until` later, with `agent_id` only if it's that agent's.
        Returns it, or None if there was no such open follow-up or `until` isn't later than
        its own. One guarded UPDATE; the dates compare as YYYY-MM-DD text, so anything but
        a date raises ValueError first.

        Only `until` changes: the counts, the fingerprint and the last report stay. The
        near-until rule (`finish_check`), `due` and `expire` read the new one on their own.
        """
        day = date.fromisoformat(until).isoformat()
        sql = "UPDATE follow_ups SET until = ? WHERE id = ? AND status = 'open' AND until < ?"
        params: List[Any] = [day, follow_up_id, day]
        if agent_id is not None:
            sql += " AND agent_id = ?"
            params.append(agent_id)
        if self.execute(sql, params) != 1:
            return None
        return self.get(follow_up_id)

    def get(self, follow_up_id: int) -> Optional[FollowUp]:
        record = self.select_one("follow_ups", {"id": follow_up_id})
        return _follow_up(record) if record else None

    def list(self, agent_id: Optional[str] = None, status: Optional[Status] = None) -> List[FollowUp]:
        """The follow-ups, oldest first: one agent's, of one status, or every one."""
        where: Dict[str, Any] = {}
        if agent_id is not None:
            where["agent_id"] = agent_id
        if status is not None:
            where["status"] = status
        return [_follow_up(record) for record in self.select("follow_ups", where or None, order_by="id ASC")]

    def closed(self, limit: int) -> List[FollowUp]:
        """The last `limit` follow-ups closed, newest first."""
        records = self.select("follow_ups", {"status": Status.CLOSED}, order_by="closed_at DESC, id DESC",
                              limit=limit)
        return [_follow_up(record) for record in records]

    # -- the checks (core/proactive/follow_ups.py) ----------------------------------

    def due(self, now: float, today: str, limit: Optional[int] = None) -> List[FollowUp]:
        """The open follow-ups whose check is due by `now`, unclaimed, and whose `until` isn't
        before `today` (YYYY-MM-DD, your wall clock): the longest due first, at most `limit`."""
        sql = ("SELECT * FROM follow_ups WHERE status = 'open' AND claimed_at IS NULL AND next_check_at <= ? "
               "AND until >= ? ORDER BY next_check_at ASC, id ASC")
        params: List[Any] = [now, today]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [_follow_up(record) for record in self.query(sql, params)]

    def claim(self, follow_up_id: int, now: float) -> bool:
        """Takes a due check for one run; True only for the claim that changed the row.

        One conditional UPDATE: of two ticks claiming at once, only one does.
        """
        return self.execute("UPDATE follow_ups SET claimed_at = ? WHERE id = ? AND status = 'open' "
                            "AND claimed_at IS NULL", [now, follow_up_id]) == 1

    def release_stale(self, now: float, older_than_s: float) -> List[int]:
        """Frees the claims taken before `now - older_than_s`, and returns their ids: a check
        whose run never finished (a restart, or another host took the agent) is due again."""
        cutoff, released = now - older_than_s, []
        with self.connect() as conn:
            stale = [r[0] for r in conn.execute("SELECT id FROM follow_ups WHERE claimed_at < ? ORDER BY id",
                                                [cutoff])]
            for follow_up_id in stale:
                if conn.execute("UPDATE follow_ups SET claimed_at = NULL WHERE id = ? AND claimed_at < ?",
                                [follow_up_id, cutoff]).rowcount:
                    released.append(follow_up_id)
        return released

    def finish_check(self, follow_up_id: int, result: Result, now: float, *, fingerprint: Optional[str] = None,
                     report: Optional[str] = None) -> Optional[FollowUp]:
        """Counts how a check went, frees its claim, and sets when the next one is due.
        Returns the follow-up, or None if there's no such follow-up.

          FOUND    one more check and one more find; the empty checks in a row and
                   the interval start over (`every`)
          EMPTY    one more check, and one more empty in a row: every EMPTY_STREAK-th
                   doubles the interval, up to MAX_INTERVAL_S
          PROBLEM, FAILED  nothing counted

        A `fingerprint` given is kept on FOUND and EMPTY: findings the agent judged
        not worth a post don't come back as new. A `report` given is kept on FOUND.
        Then, when less than two intervals are left before `until` ends, the interval
        is back to `every`, and the next check is due one interval from now.

        A follow-up closed during its own check (the check met the goal and closed
        it, or you cancelled it meanwhile) still counts that check: its closed line
        says what it found. It just isn't due again.
        """
        result = Result(result)  # anything but a Result raises ValueError
        with self.connect() as conn:
            record = conn.execute("SELECT * FROM follow_ups WHERE id = ?", [follow_up_id]).fetchone()
            if record is None:
                return None
            current = _follow_up(record)
            values: Dict[str, Any] = {"claimed_at": None, "last_checked_at": now}
            if result is Result.FOUND:
                values.update(checks=current.checks + 1, finds=current.finds + 1, empty_checks=0, last_found_at=now)
            elif result is Result.EMPTY:
                values.update(checks=current.checks + 1, empty_checks=current.empty_checks + 1)
            if fingerprint is not None and result in (Result.FOUND, Result.EMPTY):
                values["last_fingerprint"] = fingerprint
            if report is not None and result is Result.FOUND:
                values["last_report"] = report
            if current.is_open:
                interval = current.interval_s
                if result is Result.FOUND:
                    interval = current.every_s
                elif result is Result.EMPTY and values["empty_checks"] % EMPTY_STREAK == 0:
                    interval = min(interval * 2, MAX_INTERVAL_S)
                if end_of(current.until) - now < 2 * interval:
                    interval = current.every_s
                values.update(interval_s=interval, next_check_at=now + interval)
            self.update("follow_ups", values, {"id": follow_up_id}, conn=conn)
        return self.get(follow_up_id)

    def expire(self, today: str, now: Optional[float] = None) -> List[FollowUp]:
        """Closes the open follow-ups whose `until` is before `today` (YYYY-MM-DD, your wall
        clock), as "until passed", and returns them. Ending isn't a check: the service's
        sweep runs this with the switch off too."""
        ids = [r[0] for r in self.query("SELECT id FROM follow_ups WHERE status = 'open' AND until < ? "
                                        "ORDER BY id", [today])]
        ended = [self.close(follow_up_id, reason=UNTIL_PASSED, by=Closer.UNTIL, at=now) for follow_up_id in ids]
        return [follow_up for follow_up in ended if follow_up is not None]

    # -- team radio's outbox (core/proactive/follow_ups.py, FollowUpSweep) -----------

    def pending_lines(self) -> List[FollowUp]:
        """The follow-ups whose team radio line isn't out: opened with no line yet, or closed
        without its closed line (`radio_state`), oldest first. A closed one whose check is
        still running waits for that check to finish, so its line counts it."""
        records = self.query("SELECT * FROM follow_ups WHERE (status = 'open' AND radio_state = '') "
                             "OR (status = 'closed' AND radio_state != 'closed' AND claimed_at IS NULL) ORDER BY id")
        return [_follow_up(record) for record in records]

    def mark_line(self, follow_up_id: int, state: Status) -> None:
        """Records the last team radio line a follow-up had: open or closed."""
        self.update("follow_ups", {"radio_state": Status(state)}, {"id": follow_up_id})
