import time
from dataclasses import dataclass
from typing import Dict, List, Any, Optional
import contextvars
import os
import json
import sqlite3
from contextlib import contextmanager

current_job_id = contextvars.ContextVar("current_job_id", default=None)
current_channel_name = contextvars.ContextVar("current_channel_name", default="")
current_agent_id = contextvars.ContextVar("current_agent_id", default=None)
current_graph_id = contextvars.ContextVar("current_graph_id", default=None)

SESSIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "sessions"))
DEFAULT_DB_PATH = os.path.join(SESSIONS_DIR, "memory.db")


@dataclass
class Job:
    job_id: str
    agent_id: str
    session_id: str
    started: float
    updated: float
    status: str
    initial_prompt: str = ""


class JobManager:
    _instance = None

    def __new__(cls, db_path: str = DEFAULT_DB_PATH):
        if cls._instance is None:
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
                initial_prompt TEXT DEFAULT ''
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
                            job_id, agent_id, session_id, started, updated, status, initial_prompt
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (
                            job_data.get("job_id", jid),
                            job_data.get("agent_id", ""),
                            job_data.get("session_id", ""),
                            job_data.get("started", time.time()),
                            job_data.get("updated", time.time()),
                            job_data.get("status", "completed"),
                            job_data.get("initial_prompt", "")
                        ))
                    conn.commit()
                os.remove(legacy_file)
            except Exception as e:
                print(f"Error migrating legacy jobs.json: {e}")

    def _load_jobs(self):
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT job_id, agent_id, session_id, started, updated, status, initial_prompt FROM jobs ORDER BY updated ASC"
                )
                for row in cursor.fetchall():
                    job = Job(
                        job_id=row["job_id"],
                        agent_id=row["agent_id"],
                        session_id=row["session_id"],
                        started=row["started"],
                        updated=row["updated"],
                        status=row["status"],
                        initial_prompt=row["initial_prompt"] or ""
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
                "SELECT job_id, agent_id, session_id, started, updated, status, initial_prompt FROM jobs WHERE job_id = ?",
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
                    initial_prompt=row["initial_prompt"] or ""
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

    def new_job_id(self, agent_id: str) -> str:
        import uuid
        job_id = f"{agent_id}:job:{uuid.uuid4().hex[:8]}"
        self._job_ids.append(job_id)
        return job_id

    def _clean_jobs(self):
        to_remove = []
        for jid in list(self._job_ids):
            if jid in self._jobs:
                job = self._jobs[jid]
                if job.status in ["completed", "error", "partial"]:
                    to_remove.append(jid)
            else:
                to_remove.append(jid)
        for jid in to_remove:
            if jid in self._job_ids:
                self._job_ids.remove(jid)
            if jid in self._jobs:
                del self._jobs[jid]

        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM jobs WHERE status IN ('completed', 'error', 'partial')")
                conn.commit()
        except Exception as e:
            print(f"Error cleaning jobs in sqlite: {e}")

    def add_job(self, job_id: str, agent_id: str, session_id: str, initial_prompt: str = ""):
        if len(self._job_ids) > 50:
            self._clean_jobs()
        now = time.time()
        job = Job(
            job_id=job_id,
            agent_id=agent_id,
            session_id=session_id,
            started=now,
            updated=now,
            status="queued",
            initial_prompt=initial_prompt
        )
        self._jobs[job_id] = job
        if job_id not in self._job_ids:
            self._job_ids.append(job_id)

        try:
            with self._get_connection() as conn:
                conn.execute("""
                INSERT OR REPLACE INTO jobs (
                    job_id, agent_id, session_id, started, updated, status, initial_prompt
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (job_id, agent_id, session_id, now, now, "queued", initial_prompt))
                conn.commit()
        except Exception as e:
            print(f"Error saving job {job_id}: {e}")

    def get_jobs(self, allowlist: List[str] = ["queued", "running", "error", "partial"]) -> List[Job]:
        filtered_jobs = []
        for job in self._jobs.values():
            if job.status in allowlist:
                filtered_jobs.append(job)
        return filtered_jobs