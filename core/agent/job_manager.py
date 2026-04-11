import time
from dataclasses import dataclass
from typing import Dict, List, Any

@dataclass
class Job:
    session_id: str
    original_agent_id: str
    agent_id: str
    agent_instance: Any
    started: float


class JobManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(JobManager, cls).__new__(cls)
            cls._instance._jobs: Dict[str, List[Job]] = {}
        return cls._instance

    def add_job(self, session_id: str, original_agent_id: str, agent_id: str, agent_instance: Any):
        if session_id not in self._jobs:
            self._jobs[session_id] = []
        self._jobs[session_id].append(Job(
            session_id=session_id,
            original_agent_id=original_agent_id,
            agent_id=agent_id,
            agent_instance=agent_instance,
            started=time.time()
        ))

    def remove_job(self, session_id: str):
        if session_id in self._jobs and self._jobs[session_id]:
            self._jobs[session_id].pop(0)
            if not self._jobs[session_id]:
                del self._jobs[session_id]

    def get_jobs(self) -> List[Job]:
        all_jobs = []
        for jobs in self._jobs.values():
            all_jobs.extend(jobs)
        return all_jobs