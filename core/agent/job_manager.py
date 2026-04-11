import time
from dataclasses import dataclass
from typing import Dict, List, Any

@dataclass
class Job:
    job_id: str
    agent_id: str
    session_id: str
    started: float
    updated: float
    status: str


class JobManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(JobManager, cls).__new__(cls)
            cls._instance._jobs: Dict[str, Job] = {}
            cls._instance._job_ids: List[str] = []
        return cls._instance

    def updateJob(self, job_id: str, status: str):
        if job_id in self._jobs:
            self._jobs[job_id].status = status
            self._jobs[job_id].updated = time.time()

    def new_job_id(self, agent_id: str) -> str:
        import uuid
        job_id = f"{agent_id}:job:{uuid.uuid4().hex[:8]}"
        self._job_ids.append(job_id)
        return job_id

    def _clean_jobs(self):
        to_remove = []
        for jid in self._job_ids:
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

    def add_job(self, job_id: str, agent_id: str, session_id: str):
        if len(self._job_ids) > 50:
            self._clean_jobs()
        self._jobs[job_id] = Job(
            job_id=job_id,
            agent_id=agent_id,
            session_id=session_id,
            started=time.time(),
            updated=time.time(),
            status="queued"
        )

    def get_jobs(self, allowlist: List[str] = ["queued", "running", "error", "partial"]) -> List[Job]:
        filtered_jobs = []
        for job in self._jobs.values():
            if job.status in allowlist:
                filtered_jobs.append(job)
        return filtered_jobs