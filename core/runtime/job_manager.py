import time
from dataclasses import dataclass
from typing import Dict, List, Any, Optional, Union
import contextvars
import os
import json
import sqlite3
from contextlib import contextmanager
from core.runtime.execution_context import ExecutionContext


SESSIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "sessions"))
DEFAULT_DB_PATH = os.path.join(SESSIONS_DIR, "memory.db")

# Jobs that are still in flight. Rows stuck here past a max age are reaped.
# `killing` is included so a kill that never completed (process died) can't leak.
ACTIVE_STATUSES = ("queued", "running", "killing")
# Terminal states. `timeout` is an error state written by the reaper and kept
# for record-keeping; `killed` is written after a successful kill.
TERMINAL_STATUSES = ("completed", "error", "partial", "timeout", "killed")
# Default `get_jobs()` allowlist: what an agent sees when it lists jobs.
# Deliberately excludes `timeout`, `killed` and `completed` so reaped/finished
# jobs never re-enter an agent context.
IN_MEMORY_STATUS = ("queued", "running", "error", "partial")
DEFAULT_STALE_SECONDS = 1800
TERMINAL_RETENTION_SECONDS = 7 * 24 * 3600


@dataclass
class Job:
    job_id: str
    agent_id: str
    session_id: str
    started: float
    updated: float
    status: str
    prompt: str = ""


class JobManager:
    _instance = None

    def __new__(cls, db_path: Optional[str] = None):
        if cls._instance is None:
            # Resolved at call time (not def time) so tests can redirect it.
            if db_path is None:
                db_path = DEFAULT_DB_PATH
            cls._instance = super(JobManager, cls).__new__(cls)
            cls._instance.db_path = db_path
            cls._instance._jobs: Dict[str, Job] = {}
            cls._instance._job_ids: List[str] = []
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
            cls._instance._init_db()
            cls._instance._migrate_legacy_jobs()
            cls._instance._load_jobs()
        return cls._instance

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA synchronous = NORMAL")
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                started REAL NOT NULL,
                updated REAL NOT NULL,
                status TEXT NOT NULL,
                prompt TEXT DEFAULT ''
            )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_updated ON jobs(updated)")
            conn.commit()

    def _migrate_legacy_jobs(self):
        legacy_file = os.path.join(os.path.dirname(os.path.abspath(self.db_path)), "jobs.json")
        if os.path.exists(legacy_file):
            try:
                with open(legacy_file, "r") as f:
                    data = json.load(f)
                with self._get_connection() as conn:
                    for jid, job_data in data.items():
                        conn.execute("""
                        INSERT OR IGNORE INTO jobs (
                            job_id, agent_id, session_id, started, updated, status, prompt
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (
                            job_data.get("job_id", jid),
                            job_data.get("agent_id", ""),
                            job_data.get("session_id", ""),
                            job_data.get("started", time.time()),
                            job_data.get("updated", time.time()),
                            job_data.get("status", "completed"),
                            job_data.get("prompt", "")
                        ))
                    conn.commit()
                os.remove(legacy_file)
            except Exception as e:
                print(f"Error migrating legacy jobs.json: {e}")

    def _load_jobs(self):
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT job_id, agent_id, session_id, started, updated, status, prompt FROM jobs ORDER BY updated ASC"
                )
                for row in cursor.fetchall():
                    job = Job(
                        job_id=row["job_id"],
                        agent_id=row["agent_id"],
                        session_id=row["session_id"],
                        started=row["started"],
                        updated=row["updated"],
                        status=row["status"],
                        prompt=row["prompt"] or ""
                    )
                    self._jobs[job.job_id] = job
                    if job.job_id not in self._job_ids:
                        self._job_ids.append(job.job_id)
        except Exception as e:
            print(f"Error loading jobs from sqlite: {e}")

    def get_job(self, job_id: str) -> Optional[Job]:
        if job_id in self._jobs:
            return self._jobs[job_id]
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT job_id, agent_id, session_id, started, updated, status, prompt FROM jobs WHERE job_id = ?",
                (job_id,)
            )
            row = cursor.fetchone()
            if row:
                job = Job(
                    job_id=row["job_id"],
                    agent_id=row["agent_id"],
                    session_id=row["session_id"],
                    started=row["started"],
                    updated=row["updated"],
                    status=row["status"],
                    prompt=row["prompt"] or ""
                )
                self._jobs[job_id] = job
                return job
        return None

    def update_job(self, job_id: str, status: str):
        now = time.time()
        if job_id in self._jobs:
            self._jobs[job_id].status = status
            self._jobs[job_id].updated = now

        try:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE jobs SET status = ?, updated = ? WHERE job_id = ?",
                    (status, now, job_id)
                )
                conn.commit()
        except Exception as e:
            print(f"Error updating job {job_id}: {e}")

    def kill_job(self, job_id: str):
        if job_id in self._jobs:
            self._jobs[job_id].status = "killing"
            self._jobs[job_id].updated = time.time()
        self.update_job(job_id, "killing")

    def _clean_jobs(self):
        to_remove = []
        for jid in list(self._job_ids):
            if jid in self._jobs:
                job = self._jobs[jid]
                if job.status in TERMINAL_STATUSES:
                    to_remove.append(jid)
            else:
                to_remove.append(jid)
        self._forget(to_remove)

        placeholders = ",".join("?" * len(TERMINAL_STATUSES))
        try:
            with self._get_connection() as conn:
                conn.execute(f"DELETE FROM jobs WHERE status IN ({placeholders})", TERMINAL_STATUSES)
                conn.commit()
        except Exception as e:
            print(f"Error cleaning jobs in sqlite: {e}")

    def _forget(self, job_ids: List[str]):
        for jid in job_ids:
            if jid in self._job_ids:
                self._job_ids.remove(jid)
            self._jobs.pop(jid, None)

    def has_stale(self, max_age_seconds: int = DEFAULT_STALE_SECONDS) -> bool:
        """True if any running/queued job has not been updated within max_age_seconds."""
        cutoff = time.time() - max_age_seconds
        placeholders = ",".join("?" * len(ACTIVE_STATUSES))
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    f"SELECT 1 FROM jobs WHERE status IN ({placeholders}) AND updated < ? LIMIT 1",
                    (*ACTIVE_STATUSES, cutoff),
                ).fetchone()
                return row is not None
        except Exception as e:
            print(f"Error checking stale jobs: {e}")
            return False

    def reap_stale(self, max_age_seconds: int = DEFAULT_STALE_SECONDS) -> List[str]:
        """Marks stale running/queued jobs as `timeout` and returns their ids.

        Also purges terminal jobs (completed/error/partial/timeout/killed) whose
        last update is older than the 7-day retention window.
        """
        now = time.time()
        cutoff = now - max_age_seconds
        retention_cutoff = now - TERMINAL_RETENTION_SECONDS
        active_ph = ",".join("?" * len(ACTIVE_STATUSES))
        terminal_ph = ",".join("?" * len(TERMINAL_STATUSES))
        reaped: List[str] = []
        purged: List[str] = []
        try:
            with self._get_connection() as conn:
                reaped = [r["job_id"] for r in conn.execute(
                    f"SELECT job_id FROM jobs WHERE status IN ({active_ph}) AND updated < ?",
                    (*ACTIVE_STATUSES, cutoff),
                ).fetchall()]
                if reaped:
                    conn.executemany(
                        "UPDATE jobs SET status = 'timeout', updated = ? WHERE job_id = ?",
                        [(now, jid) for jid in reaped],
                    )
                purged = [r["job_id"] for r in conn.execute(
                    f"SELECT job_id FROM jobs WHERE status IN ({terminal_ph}) AND updated < ?",
                    (*TERMINAL_STATUSES, retention_cutoff),
                ).fetchall()]
                if purged:
                    conn.execute(
                        f"DELETE FROM jobs WHERE status IN ({terminal_ph}) AND updated < ?",
                        (*TERMINAL_STATUSES, retention_cutoff),
                    )
                conn.commit()
        except Exception as e:
            print(f"Error reaping stale jobs: {e}")
            return []

        for jid in reaped:
            job = self._jobs.get(jid)
            if job is not None:
                job.status = "timeout"
                job.updated = now
        self._forget(purged)
        return reaped

    def add_job(
        self,
        session: ExecutionContext,
        prompt: str = "",
    ):
        if len(self._job_ids) > 50:
            self._clean_jobs()
        now = time.time()

        if not isinstance(session, ExecutionContext):
            raise TypeError(f"session must be an instance of ExecutionContext, got {type(session).__name__}")

        job_id = session.job_id
        job = Job(
            job_id=job_id,
            agent_id=session.agent_id,
            session_id=session.session_id,
            started=now,
            updated=now,
            status="queued",
            prompt=prompt,
        )
        self._jobs[job_id] = job
        if job_id not in self._job_ids:
            self._job_ids.append(job_id)

        try:
            with self._get_connection() as conn:
                conn.execute("""
                INSERT OR REPLACE INTO jobs (
                    job_id, agent_id, session_id, started, updated, status, prompt
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (job_id, session.agent_id, session.session_id, now, now, "queued", prompt))
                conn.commit()
        except Exception as e:
            print(f"Error saving job {job_id}: {e}")
 
    def get_jobs(self, allowlist: Optional[List[str]] = None) -> List[Job]:
        """Returns in-memory jobs whose status is in allowlist (default `IN_MEMORY_STATUS`)."""
        if allowlist is None:
            allowlist = IN_MEMORY_STATUS
        filtered_jobs = []
        for job in self._jobs.values():
            if job.status in allowlist:
                filtered_jobs.append(job)
        return filtered_jobs