"""The channel inbox: at most one row per text channel, and what a row is.

A row is the newest agent post in its channel that you may not have finished
with: a scheduled push, or a reply that needs you. Every category of a row
is an enum. Posts and read signals write the inbox (core/channel/inbox.py);
voice briefings read it (core/voice/briefing/hook.py).

Lives in `memory.db` beside the schedule state (core/storage/schedule_state.py).
The schema is core/storage/schemas/channel_inbox.sql: every category
column holds an enum's value, and the table checks it. Rows past their 24
hours are marked EXPIRED lazily, when they are read, so expiry needs no
scheduled task.
"""
import sqlite3
import time
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional

from core.storage.base import Store
from core.storage.util import placeholders
from core.util.config import Config

# Every row is skipped, and never mentioned, 24 hours after its post.
TTL_S = 24 * 3600


class Source(str, Enum):
    """What made the row."""
    SCHEDULE = "SCHEDULE"      # a push: a scheduled run, a script's output, a scan's problem notice
    REPLY = "REPLY"            # a reply to you; a candidate until the speech writer judges it
    LATE_REPLY = "LATE_REPLY"  # a reply that came 10+ minutes after your message


class Priority(str, Enum):
    """How much a push matters when you're in voice. Set per schedule entry."""
    IGNORE = "IGNORE"    # never a row
    LOW = "LOW"          # not in the digest; "next agent" reaches it last
    DEFAULT = "DEFAULT"  # in the digest
    HIGHEST = "HIGHEST"  # in the digest, and announced live while you're in voice

    @classmethod
    def parse(cls, value) -> "Priority":
        """A schedule entry's `priority`: DEFAULT when absent, any letter case."""
        if value is None:
            return cls.DEFAULT
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            try:
                return cls(value.strip().upper())
            except ValueError:
                pass
        raise ValueError(f"unknown priority {value!r} (allowed: {', '.join(p.value for p in cls)})")

    @property
    def rank(self) -> int:
        """Higher is more urgent."""
        return _RANKS[self]


_RANKS = {Priority.IGNORE: 0, Priority.LOW: 1, Priority.DEFAULT: 2, Priority.HIGHEST: 3}


class Status(str, Enum):
    """Where you are with a row."""
    UNREAD = "UNREAD"                  # not heard yet
    AWAITING_REPLY = "AWAITING_REPLY"  # heard; holds a question you haven't answered
    DONE = "DONE"                      # finished
    EXPIRED = "EXPIRED"                # 24 hours old: skipped silently


class Via(str, Enum):
    """How a row was finished."""
    VOICE = "VOICE"
    TYPED = "TYPED"
    REACTION = "REACTION"


UNFINISHED = (Status.UNREAD, Status.AWAITING_REPLY)


@dataclass(frozen=True)
class InboxRow:
    """One channel's newest agent post, and where you are with it."""
    channel: str                       # the text channel's name: the voice focus key
    agent_id: str                      # who answers there
    posted_at: float                   # epoch seconds
    source: Source
    priority: Priority = Priority.DEFAULT
    status: Status = Status.UNREAD
    schedule_id: Optional[str] = None  # for SCHEDULE rows
    label: Optional[str] = None        # how the digest names it: "your morning check-in"
    speech: Optional[str] = None       # the speech version; None until written
    question: Optional[str] = None     # its marked <question>, replayed while AWAITING_REPLY
    excerpt: Optional[str] = None      # the post itself, for the speech writer
    status_at: Optional[float] = None
    status_via: Optional[Via] = None

    def expired(self, now: Optional[float] = None) -> bool:
        if self.status is Status.EXPIRED:
            return True
        return ((time.time() if now is None else now) - self.posted_at) >= TTL_S

    def unfinished(self, now: Optional[float] = None) -> bool:
        """Something left for you: not finished, and not too old to mention."""
        return self.status in UNFINISHED and not self.expired(now)

    @property
    def is_candidate(self) -> bool:
        """A reply not judged yet. It stays only if it asks you something."""
        return self.source is Source.REPLY and self.speech is None

    @property
    def is_reply(self) -> bool:
        return self.source in (Source.REPLY, Source.LATE_REPLY)

    def with_status(self, status: Status, via: Optional[Via] = None, at: Optional[float] = None) -> "InboxRow":
        return replace(self, status=status, status_via=via, status_at=time.time() if at is None else at)


_COLUMNS = ("channel", "agent_id", "posted_at", "source", "schedule_id", "label", "speech",
            "question", "excerpt", "priority", "status", "status_at", "status_via")


def _row(record: sqlite3.Row) -> InboxRow:
    return InboxRow(
        channel=record["channel"],
        agent_id=record["agent_id"],
        posted_at=record["posted_at"],
        source=Source(record["source"]),
        priority=Priority(record["priority"]),
        status=Status(record["status"]),
        schedule_id=record["schedule_id"],
        label=record["label"],
        speech=record["speech"],
        question=record["question"],
        excerpt=record["excerpt"],
        status_at=record["status_at"],
        status_via=Via(record["status_via"]) if record["status_via"] else None,
    )


def _values(row: InboxRow) -> Dict[str, Any]:
    # The enums are stored by their values (core/storage/util.py `bind`).
    return {column: getattr(row, column) for column in _COLUMNS}


class InboxStore(Store):
    schema = ("channel_inbox",)

    def default_path(self) -> str:
        return Config().memory_db_path

    # --- writing ------------------------------------------------------------------

    def put(self, row: InboxRow) -> None:
        """The channel's row is now `row`: a newer post replaces the old one, whatever its source."""
        if row.priority is Priority.IGNORE:
            raise ValueError("an IGNORE post never becomes a row")
        self.insert("channel_inbox", _values(row), or_="REPLACE")

    def set_status(self, channel: str, status: Status, via: Optional[Via] = None, *,
                   posted_at: Optional[float] = None, posted_before: Optional[float] = None,
                   only_from: Iterable[Status] = UNFINISHED, now: Optional[float] = None) -> bool:
        """Moves the channel's row to `status`, if it is in one of `only_from`.

        `posted_at` pins the change to one post, so a newer post that replaced
        it meanwhile is left alone. `posted_before` limits it to posts no newer
        than that. True if a row changed.
        """
        statuses = tuple(only_from)
        sql = (f"UPDATE channel_inbox SET status = ?, status_via = ?, status_at = ? "
               f"WHERE channel = ? AND status IN ({placeholders(len(statuses))})")
        params = [status.value, via.value if via else None, time.time() if now is None else now,
                  channel, *statuses]
        if posted_at is not None:
            sql += " AND posted_at = ?"
            params.append(posted_at)
        if posted_before is not None:
            sql += " AND posted_at <= ?"
            params.append(posted_before)
        return self.execute(sql, params) > 0

    def set_speech(self, channel: str, posted_at: float, speech: str, question: Optional[str],
                   status: Optional[Status] = None) -> bool:
        """Stores the speech version of the channel's row, if it's still the same post.
        `status`, when given, is set too (a judged candidate that asks nothing is DONE)."""
        values: Dict[str, Any] = {"speech": speech, "question": question}
        if status is not None:
            values.update(status=status, status_at=time.time())
        return self.update("channel_inbox", values, {"channel": channel, "posted_at": posted_at}) > 0

    # --- reading ------------------------------------------------------------------

    def get(self, channel: str) -> Optional[InboxRow]:
        record = self.select_one("channel_inbox", {"channel": channel})
        return _row(record) if record else None

    def rows(self, now: Optional[float] = None) -> List[InboxRow]:
        """Every row, newest post first, with expiry applied."""
        with self.connect() as conn:
            self._expire(conn, now)
            records = self.select("channel_inbox", order_by="posted_at DESC", conn=conn)
        return [_row(r) for r in records]

    def unfinished(self, now: Optional[float] = None) -> List[InboxRow]:
        """Rows still waiting on you (UNREAD or AWAITING_REPLY), oldest post first."""
        with self.connect() as conn:
            self._expire(conn, now)
            records = self.select("channel_inbox", {"status": UNFINISHED}, order_by="posted_at ASC", conn=conn)
        return [_row(r) for r in records]

    def _expire(self, conn: sqlite3.Connection, now: Optional[float]) -> None:
        now = time.time() if now is None else now
        self.execute(
            f"UPDATE channel_inbox SET status = ?, status_at = ?, status_via = NULL "
            f"WHERE status IN ({placeholders(len(UNFINISHED))}) AND posted_at <= ?",
            (Status.EXPIRED, now, *UNFINISHED, now - TTL_S),
            conn=conn,
        )
