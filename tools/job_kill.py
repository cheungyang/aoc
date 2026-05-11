import time
from langchain_core.tools import tool
from core.agent.job_manager import JobManager
from core.util import format_tool_response
from core.memory.flat_file_checkpointer import FlatFileCheckpointer

@tool
def job_kill(job_id: str) -> str:
    """
    Kills a background specialization job by job_id and returns intermediate results.
    This is a blocking call that waits for the job to stop.
    """
    try:
        manager = JobManager()
        job = manager._jobs.get(job_id)
        if not job:
            return format_tool_response("job_kill", payload="", errors=f"Job {job_id} not found.")
            
        session_id = job.session_id
        
        # Mark for killing
        manager.kill_job(job_id)
        
        # Wait for it to be killed (polling)
        timeout = 10 # seconds
        start_time = time.time()
        while time.time() - start_time < timeout:
            job = manager._jobs.get(job_id)
            if job and job.status == "killed":
                break
            time.sleep(0.5)
            
        if job.status != "killed":
             return format_tool_response("job_kill", payload=f"Job {job_id} did not stop in time. Current status: {job.status}", errors="None")

        # Retrieve state from checkpointer
        checkpointer = FlatFileCheckpointer()
        config = {"configurable": {"thread_id": session_id}}
        checkpoint_tuple = checkpointer.get_tuple(config)
        
        if not checkpoint_tuple:
            return format_tool_response("job_kill", payload=f"Job {job_id} killed, but no checkpoint found for session {session_id}.", errors="None")
            
        checkpoint = checkpoint_tuple.checkpoint
        channel_values = checkpoint.get("channel_values", {})
        
        # Extract messages
        messages = channel_values.get("messages", [])
        
        # Format a summary
        summary = f"Job {job_id} killed successfully.\n"
        summary += f"Intermediate results (Session: {session_id}):\n"
        
        if isinstance(messages, list):
            # Show last 5 messages or all if less than 5
            start_idx = max(0, len(messages) - 5)
            for msg in messages[start_idx:]:
                role = "unknown"
                content = ""
                
                # Check for standard LangChain message attributes
                if hasattr(msg, "role"):
                    role = msg.role
                elif isinstance(msg, dict) and "role" in msg:
                    role = msg["role"]
                elif hasattr(msg, "type"): # LangChain messages often have 'type' instead of 'role'
                    role = msg.type
                    
                if hasattr(msg, "content"):
                    content = msg.content
                elif isinstance(msg, dict) and "content" in msg:
                    content = msg["content"]
                else:
                    content = str(msg)
                    
                summary += f"--- {role} ---\n{content}\n"
        else:
             summary += f"Raw state: {str(messages)}\n"
             
        return format_tool_response("job_kill", payload=summary, errors="None")
        
    except Exception as e:
        return format_tool_response("job_kill", payload="", errors=f"Error killing job: {e}")
