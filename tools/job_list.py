from datetime import datetime
from langchain_core.tools import tool
from core.agent.job_manager import JobManager
from core.util import format_tool_response

@tool
def job_list() -> str:
    """
    Returns a list of active background specialization jobs.
    Output fields array: job_id, agent_id, session_id, started, status.
    """
    try:
        jobs = [
            {
                "agent_id": job.agent_id,
                "job_id": job.job_id,
                "session_id": job.session_id,
                "started": datetime.fromtimestamp(job.started).strftime('%Y-%m-%d %H:%M:%S'),
                "status": job.status,
            }
            for job in JobManager().get_jobs()
        ]
        return format_tool_response("job_list", payload=str(jobs), errors="None")
    except Exception as e:
        return format_tool_response("job_list", payload="", errors=f"Error listing jobs: {e}")

