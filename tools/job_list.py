from datetime import datetime
from langchain_core.tools import tool
from core.agent.job_manager import JobManager

@tool
def job_list() -> list[dict]:
    """
    Returns a list of active background specialization jobs.
    Output fields array: session_id, agent_id, started, status.
    """
    manager = JobManager()
    return [
        {
            "agent_id": jobs[0].agent_id,
            "started": datetime.fromtimestamp(jobs[0].started).strftime('%Y-%m-%d %H:%M:%S'),
            "status": jobs[0].agent_instance.get_graph_status() if hasattr(jobs[0].agent_instance, 'get_graph_status') else 'idle',
            "jobs_in_queue": len(jobs) - 1
        }
        for session_id, jobs in manager._jobs.items()
    ]


