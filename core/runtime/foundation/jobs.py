import time
from dataclasses import asdict, dataclass
from typing import Dict, List, Any, Optional, Union
import contextvars
import os
import json
from core.runtime.foundation.context import ExecutionContext
from core.storage.jobs import JobsStore
from core.config.settings import Config


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


def _job(row) -> Job:
    return Job(
        job_id=row["job_id"],
        agent_id=row["agent_id"],
        session_id=row["session_id"],
        started=row["started"],
        updated=row["updated"],
        status=row["status"],
        prompt=row["prompt"] or ""
    )


class JobManager:
    _instance = None

    def __new__(cls, path: Optional[str] = None):
        if cls._instance is None:
            # Resolved at call time (not import time) so tests can redirect it,
            # and only once: the cache mirrors one file.
            store = JobsStore(path or Config().memory_db_path)
            cls._instance = super(JobManager, cls).__new__(cls)
            cls._instance._store = store
            cls._instance._jobs: Dict[str, Job] = {}
            cls._instance._job_ids: List[str] = []
            cls._instance._store.init()
            cls._instance._migrate_legacy_jobs()
            cls._instance._load_jobs()
        return cls._instance

    def _migrate_legacy_jobs(self):
        legacy_file = os.path.join(os.path.dirname(os.path.abspath(self._store.path)), "jobs.json")
        if os.path.exists(legacy_file):
            try:
                with open(legacy_file, "r") as f:
                    data = json.load(f)
                self._store.add_missing([
                    {
                        "job_id": job_data.get("job_id", jid),
                        "agent_id": job_data.get("agent_id", ""),
                        "session_id": job_data.get("session_id", ""),
                        "started": job_data.get("started", time.time()),
                        "updated": job_data.get("updated", time.time()),
                        "status": job_data.get("status", "completed"),
                        "prompt": job_data.get("prompt", ""),
                    }
                    for jid, job_data in data.items()
                ])
                os.remove(legacy_file)
            except Exception as e:
                print(f"Error migrating legacy jobs.json: {e}")

    def _load_jobs(self):
        try:
            for row in self._store.rows():
                job = _job(row)
                self._jobs[job.job_id] = job
                if job.job_id not in self._job_ids:
                    self._job_ids.append(job.job_id)
        except Exception as e:
            print(f"Error loading jobs from sqlite: {e}")

    def get_job(self, job_id: str) -> Optional[Job]:
        if job_id in self._jobs:
            return self._jobs[job_id]
        row = self._store.get(job_id)
        if row:
            job = _job(row)
            self._jobs[job_id] = job
            return job
        return None

    def job(self, job_id: str) -> Optional[Job]:
        """This process's record of a job, or None. Unlike `get_job` it never
        reads the store, so it is cheap enough to call before every tool call."""
        return self._jobs.get(job_id)

    def is_killed(self, job_id: str) -> bool:
        """Whether the job stopped because it was killed."""
        job = self._jobs.get(job_id)
        return job is not None and job.status == "killed"

    def honor_kill(self, job_id: str) -> bool:
        """Marks the job killed if a kill was requested for it (`kill_job`), and
        says whether one was. A running job calls this at its safe points --
        before every tool call -- and stops when it returns True."""
        job = self._jobs.get(job_id)
        if job is None or job.status != "killing":
            return False
        self.update_job(job_id, "killed")
        return True

    def update_job(self, job_id: str, status: str):
        now = time.time()
        if job_id in self._jobs:
            self._jobs[job_id].status = status
            self._jobs[job_id].updated = now

        try:
            self._store.set_status(job_id, status, now)
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

        try:
            self._store.delete_status(TERMINAL_STATUSES)
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
        try:
            return self._store.has_stale(ACTIVE_STATUSES, cutoff)
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
        try:
            reaped, purged = self._store.reap(ACTIVE_STATUSES, cutoff, TERMINAL_STATUSES, retention_cutoff, now)
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
            self._store.put(asdict(job))
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