"""Per-schedule state: when each schedule last *succeeded*.

`files_changed_since` and friends compare against this, so it is written only
after a successful run — a failed run must leave its work visible to the next
check rather than silently marking it done.

The recorded time is when the successful run *started*, not when it ended: a
file that changed while the run was in flight may have been missed by it, and
must still count as "changed since" next time.

Lives in its own `schedule_state` table in `memory.db`, next to the jobs table.
"""
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Dict, Optional


def default_db_path() -> str:
    # Looked up at call time, not import time, so anything that repoints the
    # session store (the test suite does) repoints this too.
    from core.knowledge.memory import sqlite_session_store
    return sqlite_session_store.DEFAULT_DB_PATH


class ScheduleState:
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schedule_state (
                    schedule_id TEXT PRIMARY KEY,
                    last_success_at REAL,
                    last_run_at REAL,
                    last_skip_at REAL,
                    last_skip_reason TEXT,
                    updated_at REAL NOT NULL
                )
                """
            )
            yield conn
            conn.commit()
        finally:
            conn.close()

    def get(self, schedule_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM schedule_state WHERE schedule_id = ?", (schedule_id,)
            ).fetchone()
        return dict(row) if row else {}

    def get_last_success(self, schedule_id: str) -> Optional[float]:
        return self.get(schedule_id).get("last_success_at")

    def _upsert(self, schedule_id: str, **values) -> None:
        now = time.time()
        columns = ["schedule_id", "updated_at"] + list(values)
        params = [schedule_id, now] + list(values.values())
        updates = ", ".join(f"{c} = excluded.{c}" for c in columns[1:])
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO schedule_state ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)}) "
                f"ON CONFLICT(schedule_id) DO UPDATE SET {updates}",
                params,
            )

    def record_success(self, schedule_id: str, started_at: float) -> None:
        """The only way last_success_at moves."""
        self._upsert(schedule_id, last_success_at=started_at, last_run_at=started_at)

    def record_failure(self, schedule_id: str, started_at: float) -> None:
        """Notes the attempt without touching last_success_at."""
        self._upsert(schedule_id, last_run_at=started_at)

    def record_skip(self, schedule_id: str, reason: str, at: Optional[float] = None) -> None:
        self._upsert(schedule_id, last_skip_at=at or time.time(), last_skip_reason=reason)
