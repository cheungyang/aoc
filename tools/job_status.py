from langchain_core.tools import tool
from core.agent.job_manager import JobManager
from core.util import format_tool_response
from core.memory.flat_file_session_store import FlatFileSessionStore

@tool
def job_status(job_id: str) -> str:
    """
    Retrieves the current progress of a background job.
    
    Args:
        job_id: The ID of the job to query.
        
    Returns:
        A string containing the status response from the executing agent, 
        including steps done, % of progress, and early snippets of completed artifacts.
    """
    try:
        manager = JobManager()
        job = manager._jobs.get(job_id)
        if not job:
            return format_tool_response("job_status", payload="", errors=f"Job {job_id} not found.")
            
        session_id = job.session_id

        
        # Use FlatFileSessionStore to get history
        session_store = FlatFileSessionStore()
        history = session_store.load_history(session_id, limit=100)
        
        if not history:
            return format_tool_response("job_status", payload=f"No session history found for session {session_id}.", errors="None")
            
        summary = f"Job {job_id} status (Session: {session_id}):\n"
        
        # Find the last AI message as it likely contains the agent's current state/response
        last_ai_msg = None
        for entry in reversed(history):
            if entry.get("from") == "ai":
                last_ai_msg = entry
                break
                
        if last_ai_msg:
            content = last_ai_msg.get("message", "")
            summary += f"Latest response from agent:\n{content}\n"
        else:
            # Fallback to last message if no AI message found
            msg = history[-1]
            content = msg.get("message", str(msg))
            summary += f"Latest message (from {msg.get('from')}):\n{content}\n"
        
        return format_tool_response("job_status", payload=summary, errors="None")
        
    except Exception as e:
        return format_tool_response("job_status", payload="", errors=f"Error getting job status: {e}")
