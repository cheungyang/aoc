"""Follow-ups in memory.db: each agent's own open loops, and what a follow-up is.

A follow-up is one of two kinds (Kind), for work the agent can do itself. It
exists only while there's a need. The follow_up tool (tools/follow_up.py) opens
one for the calling agent, extends it, closes it, and lists the open ones;
`!followups` lists them all, and extends or cancels one
(core/runtime/commands/typed/followups.py). The Switchboard closes one when
your own words to another teammate end it, in its channel or with "@Name …"
(core/proactive/pipeline.py).

A watch is a goal, a check, a cadence and an end: checking, searching,
re-evaluating. Its end, `until`, is its last day on your wall clock: from today
to MAX_DAYS out (`parse_until`, which the tool and `!followups` share). It ends
at the midnight after (`end_of`), and its first check must come before then
(`ensure_first_check`): one that never checks would never offer to keep
watching. `extend` moves it later and keeps the history: the counts, the
fingerprint and what the agent last told you. Closing and opening again would
lose them, and old findings would look new.

A one-off is a topic you asked the agent to bring back to you at a set time
(`open_once`): its goal, when (`due_at`) and the agent's notes. It has no
cadence (`every_s` 0) and is due once, at `due_at`; its check is a model check
on its goal, which nothing scans. Its `until` is the day after `due_at`
(`grace_until`): a host that was down at the due time still brings it up before
`expire` ends it. Its run closes it (`finish_check`, Closer.FIRED), so it never
offers to keep watching, and it can't be extended: moving it is closing it and
opening another.

Every read returns a FollowUp: every column but `check_key`, with its check a
Check and each fixed vocabulary an enum (Kind, Status, Closer). A check is
stored as JSON, {"type": "model", "what": ...} or a scan's {"type": <scan>,
...args}: `Check.from_spec` reads that form and `Check.spec` writes it. The
table predates one-offs, so the store adds the columns an older one lacks
(_ADDED_COLUMNS).

Two follow-ups are the same when their `check_key`s match: a watch's is the
agent and its check, normalised (`check_key`); a one-off's, the agent, its due
time and its goal (`once_key`), so a watch and a one-off never match. While
one is open, opening the same again raises DuplicateFollowUp, naming the open
one; the partial unique index in the schema (core/storage/schemas/follow_ups.sql)
makes that atomic.

The checks run on the schedule runner (core/proactive/follow_ups.py):
  - `due` lists the checks due now, `claim` takes one for one run, and
    `release_stale` frees a claim whose run never finished;
  - `finish_check` counts how a check went (a Result) and sets when the next
    one is due. A find puts the interval back to `every`; every EMPTY_STREAK-th
    empty check in a row doubles it, up to MAX_INTERVAL_S; and when less than
    two intervals are left before `until` ends, it's back to `every`, so the
    last days are watched as closely as the first. A one-off's run that posted
    or stayed silent closes it; one that failed is tried again RETRY_S later,
    until ONCE_ATTEMPTS have failed;
  - `expire` closes the ones whose `until` has passed.
Team radio announces each one opened and each one closed, once (`pending_lines`,
`mark_line`). `cadence_text`, `when_text`, `Check.text`, `FollowUp.tally` and
`FollowUp.closed_why` word a follow-up the same way for the tool, `!followups`,
team radio and Live Context.
"""
import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as clock, timedelta
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional

from core.config.settings import Config
from core.storage.base import WAL_PRAGMAS, Row, Store
from core.util.local_time import get_local_timezone

UNTIL_PASSED = "until passed"
# The furthest `until` can be, in days from today: a follow-up has an end in sight. A
# one-off's time is at most as far out.
MAX_DAYS = 90
# The check that is a short model turn. Any other type is one of the agent's scans.
MODEL_CHECK = "model"
# Empty checks in a row that double the interval (the 3rd, the 6th, ...), and the
# longest it gets: `every`'s own upper bound.
EMPTY_STREAK = 3
MAX_INTERVAL_S = 7 * 86400
# A one-off whose run failed is tried again this much later, until this many runs have
# failed: you asked for it, so one failure doesn't drop it, but a broken run stops trying.
RETRY_S = 15 * 60
ONCE_ATTEMPTS = 3
# Why a one-off's own run closed it (Closer.FIRED): it posted, it stayed silent because
# its notes made it conditional, or ONCE_ATTEMPTS runs failed.
BROUGHT_UP = "brought up"
NOTHING_TO_BRING_UP = "nothing to bring up"
NOT_BROUGHT_UP = "couldn't bring it up"
# How a one-off that `until` ended reads: its time came and went, and nothing brought it up.
NEVER_BROUGHT_UP = "never brought up"
# A moment closer than this is named by its weekday; one further out, by its date (`when_text`).
WEEKDAY_S = 6 * 86400
# Columns added to follow_ups after it shipped, with their definitions, as follow_ups.sql has
# them. The schema makes a missing table whole, but never changes one a database already holds.
_ADDED_COLUMNS = (
    ("kind", "TEXT NOT NULL DEFAULT 'watch' CHECK (kind IN ('watch', 'once'))"),
    ("due_at", "REAL"),
    ("notes", "TEXT NOT NULL DEFAULT ''"),
)


class Kind(str, Enum):
    """What a follow-up is (`kind`)."""
    WATCH = "watch"  # a check on a cadence, until an end date
    ONCE = "once"    # a topic brought back to you once, at `due_at`


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
    FIRED = "fired"              # a one-off's own run: brought up, nothing to bring up, or failed (`finish_check`)


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
    column of follow_ups.sql but `check_key`, which only the store uses.

    A one-off (Kind.ONCE) uses the same columns its own way: its check is a model check
    on its goal, its cadence is 0, its next check is `due_at`, its `until` is the day
    after, and its checks are its attempts."""
    id: int                                  # its number: "follow-up #12"
    agent_id: str                            # whose loop it is: of the agents, only it closes it
    goal: str                                # what the user wants, in the agent's words
    check: Check                             # what each check runs (`check_spec`)
    every_s: int                             # the cadence the agent asked for, in seconds; 0 for a one-off
    interval_s: int                          # the cadence now: every_s at open, then the checks adapt it
    until: str                               # YYYY-MM-DD, on your wall clock: its last day; `extend` moves it later
    opened_at: float                         # epoch seconds
    next_check_at: float                     # when its check is due: opened_at + every_s at first; a one-off's due_at
    close_when: str = ""                     # what would end it early, in words; "" if nothing
    status: Status = Status.OPEN             # open, then closed for good
    closed_at: Optional[float] = None        # epoch seconds; None while open
    closed_by: Optional[Closer] = None       # who closed it; None while open
    close_reason: str = ""                   # why, in its closer's words; "" if none was given
    channel: str = ""                        # where it was opened, and where what it finds will post
    session_id: str = ""                     # the session it was opened in
    checks: int = 0                          # checks run; a one-off's attempts, failed ones too
    finds: int = 0                           # checks that found something new
    empty_checks: int = 0                    # checks in a row that found nothing new
    last_checked_at: Optional[float] = None  # when the last check ran
    last_found_at: Optional[float] = None    # when a check last found something new
    last_fingerprint: str = ""               # what the last check found, so only something new posts
    last_report: str = ""                    # what the last find posted, clipped
    claimed_at: Optional[float] = None       # when a tick took its due check; None while no check holds it
    radio_state: Optional[Status] = None     # the last team radio line posted for it; None before the first
    kind: Kind = Kind.WATCH                  # a watch, or a one-off
    due_at: Optional[float] = None           # a one-off's: when it's brought up, epoch seconds; None for a watch
    notes: str = ""                          # a one-off's: the context its agent wrote for then; "" for a watch

    @property
    def is_open(self) -> bool:
        return self.status is Status.OPEN

    @property
    def is_once(self) -> bool:
        return self.kind is Kind.ONCE

    def tally(self) -> str:
        """What its checks came to: "6 checks, 1 find". A one-off's are its attempts, worth
        saying only when it took more than one: "3 attempts", else ""."""
        checks, finds = self.checks, self.finds
        if self.is_once:
            return f"{checks} attempts" if checks > 1 else ""
        return f"{checks} check{'' if checks == 1 else 's'}, {finds} find{'' if finds == 1 else 's'}"

    def closed_why(self, who: str) -> str:
        """Why it closed, in words: its agent's reason, or "<who> closed it"; "you cancelled
        it", with your why; "you said it's done", with the Switchboard's reason, when your own
        words ended it; or "until passed", "never brought up" for a one-off. A one-off's own
        run says how it went: "brought up", "nothing to bring up" or "couldn't bring it up"
        (`finish_check`). `who` names its agent."""
        reason = self.close_reason
        if self.closed_by is Closer.YOU:
            return f"you cancelled it: {reason}" if reason else "you cancelled it"
        if self.closed_by is Closer.UNTIL:
            return NEVER_BROUGHT_UP if self.is_once else UNTIL_PASSED
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


def when_text(at: float, since: float) -> str:
    """A moment on your wall clock, as seen from `since`: "Thu 09:00" closer than WEEKDAY_S,
    "Nov 12 09:00" further out, where a weekday alone would mislead. `!followups` says a next
    check this way; it, team radio and Live Context say a one-off's time this way too."""
    moment = datetime.fromtimestamp(at, get_local_timezone())
    day = f"{moment:%a}" if at - since < WEEKDAY_S else f"{moment:%b} {moment.day}"
    return f"{day} {moment:%H:%M}"


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


def grace_until(due_at: float) -> str:
    """A one-off's `until`: the day after its due day, on your wall clock. A host that was
    down at `due_at` still brings it up when it's back, through that next day; `expire`
    ends it after. Counted in days, not seconds: the day the clocks fall back has 25 hours."""
    due_day = datetime.fromtimestamp(due_at, get_local_timezone()).date()
    return (due_day + timedelta(days=1)).isoformat()


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


def once_key(agent_id: str, goal: str, due_at: float) -> str:
    """What makes two one-offs the same: the agent, the second it's due and its goal,
    normalised. The same goal at another time is another one-off, and a watch's key, the
    agent and a JSON object, never looks like this."""
    return f"{agent_id}:once:{int(due_at)}:{_normalised(goal)}"


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
        kind=Kind(record["kind"]),
        due_at=record["due_at"],
        notes=record["notes"],
    )


class DuplicateFollowUp(Exception):
    """An open follow-up is already this one: the same check, or a one-off with the same goal
    at the same time. `existing` is that follow-up."""

    def __init__(self, existing: FollowUp):
        self.existing = existing
        super().__init__(f"follow-up #{existing.id} is already open for this check: \"{existing.goal}\"")


class FollowUpStore(Store):
    schema = ("follow_ups",)
    pragmas = WAL_PRAGMAS

    def default_path(self) -> str:
        return Config().memory_db_path

    def _create(self, conn: sqlite3.Connection, **identifiers: str) -> None:
        """The schema, then the columns an older table lacks (_ADDED_COLUMNS), as
        SignalStore._create adds its own."""
        super()._create(conn, **identifiers)
        for column, definition in _ADDED_COLUMNS:
            if column in self._columns(conn):
                continue
            try:
                conn.execute(f"ALTER TABLE follow_ups ADD COLUMN {column} {definition}")
            except sqlite3.OperationalError:
                if column not in self._columns(conn):  # else another connection added it first
                    raise

    @staticmethod
    def _columns(conn: sqlite3.Connection) -> List[str]:
        return [row[1] for row in conn.execute("PRAGMA table_info(follow_ups)")]

    def open(self, *, agent_id: str, goal: str, check: Check, every_s: int, until: str,
             close_when: str = "", channel: str = "", session_id: str = "",
             at: Optional[float] = None) -> FollowUp:
        """Opens a watch for `agent_id`, and returns it. Its cadence starts at `every_s`
        and its first check is due `every_s` from now. Raises DuplicateFollowUp while an
        open follow-up has the same check (`check_key`)."""
        at = time.time() if at is None else at
        key = check_key(agent_id, check)
        values = {"agent_id": agent_id, "goal": goal, "check_spec": json.dumps(check.spec(), ensure_ascii=False),
                  "check_key": key, "every_s": every_s, "interval_s": every_s, "until": until,
                  "close_when": close_when or "", "status": Status.OPEN, "opened_at": at, "channel": channel or "",
                  "session_id": session_id or "", "next_check_at": at + every_s}
        return self._insert_open(values)

    def open_once(self, *, agent_id: str, goal: str, due_at: float, notes: str = "", channel: str = "",
                  session_id: str = "", at: Optional[float] = None) -> FollowUp:
        """Opens a one-off for `agent_id`: `goal` brought up at `due_at` (epoch seconds), with
        the agent's `notes` for then, and returns it. It's due at `due_at`, with no cadence,
        and its `until` is the day after (`grace_until`). Its check is a model check on its
        goal: the column needs one, and a model check is the one kind nothing scans or builds.
        Raises DuplicateFollowUp while an open one-off of the agent's has the same goal at
        the same time (`once_key`)."""
        at = time.time() if at is None else at
        check = Check(type=MODEL_CHECK, what=goal)
        values = {"agent_id": agent_id, "goal": goal, "kind": Kind.ONCE, "due_at": due_at, "notes": notes or "",
                  "check_spec": json.dumps(check.spec(), ensure_ascii=False),
                  "check_key": once_key(agent_id, goal, due_at), "every_s": 0, "interval_s": 0,
                  "until": grace_until(due_at), "status": Status.OPEN, "opened_at": at, "channel": channel or "",
                  "session_id": session_id or "", "next_check_at": due_at}
        return self._insert_open(values)

    def _insert_open(self, values: Dict[str, Any]) -> FollowUp:
        """Inserts an open follow-up and returns it; DuplicateFollowUp, naming the open one,
        when the schema's unique index refuses its `check_key`."""
        key = values["check_key"]
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
        """Moves an open watch's `until` later, with `agent_id` only if it's that agent's.
        Returns it, or None if there was no such open watch or `until` isn't later than its
        own. One guarded UPDATE; the dates compare as YYYY-MM-DD text, so anything but a date
        raises ValueError first. A one-off is never extended: a later `until` would only
        stretch its grace day, not move its time.

        Only `until` changes: the counts, the fingerprint and the last report stay. The
        near-until rule (`finish_check`), `due` and `expire` read the new one on their own.
        """
        day = date.fromisoformat(until).isoformat()
        sql = "UPDATE follow_ups SET until = ? WHERE id = ? AND status = 'open' AND kind = 'watch' AND until < ?"
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

        A one-off counts every run as an attempt, and its run ends it (`_finish_once`).
        """
        result = Result(result)  # anything but a Result raises ValueError
        with self.connect() as conn:
            record = conn.execute("SELECT * FROM follow_ups WHERE id = ?", [follow_up_id]).fetchone()
            if record is None:
                return None
            current = _follow_up(record)
            if current.is_once:
                self._finish_once(current, result, now, report, conn)
            else:
                self._finish_watch(current, result, now, fingerprint, report, conn)
        return self.get(follow_up_id)

    def _finish_watch(self, current: FollowUp, result: Result, now: float, fingerprint: Optional[str],
                      report: Optional[str], conn: sqlite3.Connection) -> None:
        """finish_check for a watch, in its transaction (finish_check's docstring)."""
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
        self.update("follow_ups", values, {"id": current.id}, conn=conn)

    def _finish_once(self, current: FollowUp, result: Result, now: float, report: Optional[str],
                     conn: sqlite3.Connection) -> None:
        """finish_check for a one-off, in its transaction. Every run is one more attempt.

          FOUND    it posted: a find, with its `report`; closed, BROUGHT_UP
          EMPTY    it stayed silent, as its notes allowed: closed, NOTHING_TO_BRING_UP
          FAILED   tried again RETRY_S from now, until ONCE_ATTEMPTS have failed:
                   then closed, NOT_BROUGHT_UP (PROBLEM, which a one-off never has,
                   counts the same)

        Each close is Closer.FIRED, and guarded as `close` is: a cancel that came first
        stands. It shares the transaction that frees the claim, so no tick can take the
        one-off again in between.
        """
        attempts = current.checks + 1
        values: Dict[str, Any] = {"claimed_at": None, "last_checked_at": now, "checks": attempts}
        if result is Result.FOUND:
            values.update(finds=current.finds + 1, last_found_at=now)
            if report is not None:
                values["last_report"] = report
            why = BROUGHT_UP
        elif result is Result.EMPTY:
            why = NOTHING_TO_BRING_UP
        elif attempts >= ONCE_ATTEMPTS:
            why = NOT_BROUGHT_UP
        else:
            why = ""
            values["next_check_at"] = now + RETRY_S
        self.update("follow_ups", values, {"id": current.id}, conn=conn)
        if why:
            self.update("follow_ups", {"status": Status.CLOSED, "closed_at": now, "closed_by": Closer.FIRED,
                                       "close_reason": why}, {"id": current.id, "status": Status.OPEN}, conn=conn)

    def expire(self, today: str, now: Optional[float] = None) -> List[FollowUp]:
        """Closes the open follow-ups whose `until` is before `today` (YYYY-MM-DD, your wall
        clock), as "until passed", and returns them. Ending isn't a check: the service's
        sweep runs this with the switch off too. A one-off's `until` is its grace day, so
        one nothing brought up by its end ends here."""
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
