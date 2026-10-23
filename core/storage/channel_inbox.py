"""The channel inbox table: at most one row per text channel.

Lives in `memory.db` beside the schedule state (core/storage/schedule_state.py).
The schema is core/storage/schemas/channel_inbox.sql: every category
column holds an enum's value, and the table checks it. Rows past their 24
hours are marked EXPIRED lazily, when they are read, so expiry needs no
scheduled task.
"""
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional

from core.storage.base import Store
from core.storage.util import placeholders
from core.util.config import Config
from core.voice.briefing.model import TTL_S, UNFINISHED, InboxRow, Priority, Source, Status, Via

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
