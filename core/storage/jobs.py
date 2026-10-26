"""Agent jobs: one row per job, from queued to done, in `memory.db`.

JobManager (core/runtime/foundation/jobs.py) keeps the cache, the statuses and
what they mean, and passes the statuses in; this holds the SQL. The schema is
core/storage/schemas/jobs.sql.
"""
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

from core.storage.base import Row, Store
from core.storage.util import placeholders
from core.config.settings import Config

_COLUMNS = ("job_id", "agent_id", "session_id", "started", "updated", "status", "prompt")


def _updated_before(statuses: Sequence[str], before: float) -> Tuple[str, List[Any]]:
    """` WHERE …` and its parameters for the jobs in one of `statuses` last updated before `before`."""
    return f" WHERE status IN ({placeholders(len(statuses))}) AND updated < ?", [*statuses, before]


class JobsStore(Store):
    schema = ("jobs",)
    pragmas = ("journal_mode = WAL", "synchronous = NORMAL")

    def default_path(self) -> str:
        return Config().memory_db_path

    def rows(self) -> List[Row]:
        """Every job, least recently updated first."""
        return self.select("jobs", columns=_COLUMNS, order_by="updated ASC")

    def get(self, job_id: str) -> Optional[Row]:
        return self.select_one("jobs", {"job_id": job_id}, columns=_COLUMNS)

    def put(self, job: Mapping[str, Any]) -> None:
        """Writes the job's row, replacing the one with its id if there is one."""
        self.insert("jobs", job, or_="REPLACE")

    def add_missing(self, jobs: Iterable[Mapping[str, Any]]) -> None:
        """Adds the jobs whose id isn't there yet, all or none (the legacy jobs.json import)."""
        with self.connect() as conn:
            for job in jobs:
                self.insert("jobs", job, or_="IGNORE", conn=conn)

    def set_status(self, job_id: str, status: str, updated: float) -> bool:
        """True if the job was there."""
        return self.update("jobs", {"status": status, "updated": updated}, {"job_id": job_id}) > 0

    def delete_status(self, statuses: Sequence[str]) -> int:
        """Deletes every job in one of `statuses`, however recent; returns how many."""
        return self.delete("jobs", {"status": tuple(statuses)})

    def has_stale(self, statuses: Sequence[str], before: float) -> bool:
        """True if a job in one of `statuses` was last updated before `before`."""
        where, params = _updated_before(statuses, before)
        return bool(self.query(f"SELECT 1 FROM jobs{where} LIMIT 1", params))

    def reap(self, stale: Sequence[str], stale_before: float, expired: Sequence[str], expired_before: float,
             now: float) -> Tuple[List[str], List[str]]:
        """In one transaction: the jobs in a `stale` status last updated before
        `stale_before` become `timeout`, updated `now`; then the jobs in an
        `expired` status last updated before `expired_before` are deleted.
        Returns the ids of both: (timed out, deleted)."""
        with self.connect() as conn:
            where, params = _updated_before(stale, stale_before)
            timed_out = [row["job_id"] for row in self.query(f"SELECT job_id FROM jobs{where}", params, conn=conn)]
            if timed_out:
                conn.executemany("UPDATE jobs SET status = 'timeout', updated = ? WHERE job_id = ?",
                                 [(now, job_id) for job_id in timed_out])
            where, params = _updated_before(expired, expired_before)
            deleted = [row["job_id"] for row in self.query(f"SELECT job_id FROM jobs{where}", params, conn=conn)]
            if deleted:
                self.execute(f"DELETE FROM jobs{where}", params, conn=conn)
        return timed_out, deleted
