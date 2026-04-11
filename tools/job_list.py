from datetime import datetime
from langchain_core.tools import tool
from core.agent.job_manager import JobManager

@tool
def job_list() -> list[dict]:
    """
    Returns a list of active background specialization jobs.
    Output fields array: job_id, agent_id, session_id, started, status.
    """
    return [
        {
            "agent_id": job.agent_id,
            "job_id": job.job_id,
            "session_id": job.session_id,
            "started": datetime.fromtimestamp(job.started).strftime('%Y-%m-%d %H:%M:%S'),
            "status": job.status,
        }
        for job in JobManager().get_jobs()
    ]

