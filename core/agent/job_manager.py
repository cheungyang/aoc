import time
from dataclasses import dataclass
from typing import Dict, List, Any
import contextvars
import os
import json

current_job_id = contextvars.ContextVar("current_job_id", default=None)
current_channel_name = contextvars.ContextVar("current_channel_name", default="")
current_agent_id = contextvars.ContextVar("current_agent_id", default=None)
current_graph_id = contextvars.ContextVar("current_graph_id", default=None)

SESSIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "sessions"))
JOBS_FILE = os.path.join(SESSIONS_DIR, "jobs.json")

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

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(JobManager, cls).__new__(cls)
            cls._instance._jobs: Dict[str, Job] = {}
            cls._instance._job_ids: List[str] = []
            cls._instance._load_jobs()
        return cls._instance

    def _load_jobs(self):
        if os.path.exists(JOBS_FILE):
            try:
                with open(JOBS_FILE, "r") as f:
                    data = json.load(f)
                    for jid, job_data in data.items():
                        if "initial_prompt" not in job_data:
                            job_data["initial_prompt"] = ""
                        self._jobs[jid] = Job(**job_data)
                        if jid not in self._job_ids:
                            self._job_ids.append(jid)
            except Exception as e:
                print(f"Error loading jobs: {e}")

    def _save_jobs(self):
        try:
            os.makedirs(os.path.dirname(JOBS_FILE), exist_ok=True)
            data = {jid: {
                "job_id": j.job_id,
                "agent_id": j.agent_id,
                "session_id": j.session_id,
                "started": j.started,
                "updated": j.updated,
                "status": j.status,
                "initial_prompt": getattr(j, "initial_prompt", "")
            } for jid, j in self._jobs.items()}
            with open(JOBS_FILE, "w") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"Error saving jobs: {e}")

    def update_job(self, job_id: str, status: str):
        if job_id in self._jobs:
            self._jobs[job_id].status = status
            self._jobs[job_id].updated = time.time()
            self._save_jobs()

    def kill_job(self, job_id: str):
        if job_id in self._jobs:
            self._jobs[job_id].status = "killing"
            self._jobs[job_id].updated = time.time()
            self._save_jobs()

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
        self._save_jobs()

    def add_job(self, job_id: str, agent_id: str, session_id: str, initial_prompt: str = ""):
        if len(self._job_ids) > 50:
            self._clean_jobs()
        self._jobs[job_id] = Job(
            job_id=job_id,
            agent_id=agent_id,
            session_id=session_id,
            started=time.time(),
            updated=time.time(),
            status="queued",
            initial_prompt=initial_prompt
        )
        self._save_jobs()

    def get_jobs(self, allowlist: List[str] = ["queued", "running", "error", "partial"]) -> List[Job]:
        filtered_jobs = []
        for job in self._jobs.values():
            if job.status in allowlist:
                filtered_jobs.append(job)
        return filtered_jobs