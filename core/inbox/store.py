"""The channel inbox table: at most one row per text channel.

Lives in `memory.db` beside the schedule state (see core/scheduler/state.py
for the pattern). The schema is core/db/channel_inbox.sql: every category
column holds an enum's value, and the table checks it. Rows past their 24
hours are marked EXPIRED lazily, when they are read, so expiry needs no
scheduled task.
"""
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Iterable, List, Optional

from core.db import apply_schema
from core.inbox.model import TTL_S, UNFINISHED, InboxRow, Priority, Source, Status, Via

SCHEMA = "channel_inbox"


def default_db_path() -> str:
    # Looked up at call time, so anything that repoints the session store
    # (the test suite does) repoints this too.
    from core.knowledge.memory import sqlite_session_store
    return sqlite_session_store.DEFAULT_DB_PATH


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


def _values(row: InboxRow) -> tuple:
    return (row.channel, row.agent_id, row.posted_at, row.source.value, row.schedule_id, row.label,
            row.speech, row.question, row.excerpt, row.priority.value, row.status.value, row.status_at,
            row.status_via.value if row.status_via else None)


def _marks(statuses: Iterable[Status]) -> tuple:
    values = tuple(s.value for s in statuses)
    return values, ", ".join("?" for _ in values)


class InboxStore:
    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path

    @property
    def db_path(self) -> str:
        return self._db_path or default_db_path()

    @contextmanager
    def _connect(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            apply_schema(conn, SCHEMA)
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --- writing ------------------------------------------------------------------

    def put(self, row: InboxRow) -> None:
        """The channel's row is now `row`: a newer post replaces the old one, whatever its source."""
        if row.priority is Priority.IGNORE:
            raise ValueError("an IGNORE post never becomes a row")
        with self._connect() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO channel_inbox ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in _COLUMNS)})",
                _values(row),
            )

    def set_status(self, channel: str, status: Status, via: Optional[Via] = None, *,
                   posted_at: Optional[float] = None, posted_before: Optional[float] = None,
                   only_from: Iterable[Status] = UNFINISHED, now: Optional[float] = None) -> bool:
        """Moves the channel's row to `status`, if it is in one of `only_from`.

        `posted_at` pins the change to one post, so a newer post that replaced
        it meanwhile is left alone. `posted_before` limits it to posts no newer
        than that. True if a row changed.
        """
        values, marks = _marks(only_from)
        sql = (f"UPDATE channel_inbox SET status = ?, status_via = ?, status_at = ? "
               f"WHERE channel = ? AND status IN ({marks})")
        params = [status.value, via.value if via else None, time.time() if now is None else now,
                  channel, *values]
        if posted_at is not None:
            sql += " AND posted_at = ?"
            params.append(posted_at)
        if posted_before is not None:
            sql += " AND posted_at <= ?"
            params.append(posted_before)
        with self._connect() as conn:
            return conn.execute(sql, params).rowcount > 0

    def set_speech(self, channel: str, posted_at: float, speech: str, question: Optional[str],
                   status: Optional[Status] = None) -> bool:
        """Stores the speech version of the channel's row, if it's still the same post.
        `status`, when given, is set too (a judged candidate that asks nothing is DONE)."""
        sql = "UPDATE channel_inbox SET speech = ?, question = ?"
        params: list = [speech, question]
        if status is not None:
            sql += ", status = ?, status_at = ?"
            params += [status.value, time.time()]
        sql += " WHERE channel = ? AND posted_at = ?"
        params += [channel, posted_at]
        with self._connect() as conn:
            return conn.execute(sql, params).rowcount > 0

    # --- reading ------------------------------------------------------------------

    def get(self, channel: str) -> Optional[InboxRow]:
        with self._connect() as conn:
            record = conn.execute("SELECT * FROM channel_inbox WHERE channel = ?", (channel,)).fetchone()
        return _row(record) if record else None

    def rows(self, now: Optional[float] = None) -> List[InboxRow]:
        """Every row, newest post first, with expiry applied."""
        with self._connect() as conn:
            self._expire(conn, now)
            records = conn.execute("SELECT * FROM channel_inbox ORDER BY posted_at DESC").fetchall()
        return [_row(r) for r in records]

    def unfinished(self, now: Optional[float] = None) -> List[InboxRow]:
        """Rows still waiting on you (UNREAD or AWAITING_REPLY), oldest post first."""
        values, marks = _marks(UNFINISHED)
        with self._connect() as conn:
            self._expire(conn, now)
            records = conn.execute(
                f"SELECT * FROM channel_inbox WHERE status IN ({marks}) ORDER BY posted_at ASC", values
            ).fetchall()
        return [_row(r) for r in records]

    @staticmethod
    def _expire(conn, now: Optional[float]) -> None:
        now = time.time() if now is None else now
        values, marks = _marks(UNFINISHED)
        conn.execute(
            f"UPDATE channel_inbox SET status = ?, status_at = ?, status_via = NULL "
            f"WHERE status IN ({marks}) AND posted_at <= ?",
            (Status.EXPIRED.value, now, *values, now - TTL_S),
        )
