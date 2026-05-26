import os
from langchain_core.tools import tool
from core.agent.job_manager import JobManager
from core.util import format_tool_response
from core.memory.flat_file_checkpointer import FlatFileCheckpointer

@tool
def job_status(job_id: str, query_path: str = None) -> str:
    """
    Retrieves the current progress of a background job.
    
    Args:
        job_id: The ID of the job to query.
        query_path: Optional path provided by the user (human-in-loop) to query the executing agent for a response (e.g., a log file or status file).
        
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
        
        # If query_path is provided, read from it (Human-in-the-loop path)
        if query_path:
            if os.path.exists(query_path):
                with open(query_path, 'r') as f:
                    content = f.read()
                return format_tool_response("job_status", payload=content, errors="None")
            else:
                return format_tool_response("job_status", payload="", errors=f"Query path {query_path} not found.")
        
        # Fallback: Try to read from checkpointer to get the latest state
        checkpointer = FlatFileCheckpointer()
        config = {"configurable": {"thread_id": session_id}}
        checkpoint_tuple = checkpointer.get_tuple(config)
        
        if not checkpoint_tuple:
            return format_tool_response("job_status", payload=f"No checkpoint found for session {session_id}. Please provide a query_path to the agent's status file.", errors="None")
            
        checkpoint = checkpoint_tuple.checkpoint
        channel_values = checkpoint.get("channel_values", {})
        messages = channel_values.get("messages", [])
        
        summary = f"Job {job_id} status (Session: {session_id}):\n"
        
        if isinstance(messages, list) and len(messages) > 0:
            # Find the last AI message as it likely contains the agent's current state/response
            last_ai_msg = None
            for msg in reversed(messages):
                role = ""
                if hasattr(msg, "type"):
                    role = msg.type
                elif isinstance(msg, dict) and "role" in msg:
                    role = msg["role"]
                
                if role in ["ai", "assistant"]:
                    last_ai_msg = msg
                    break
            
            if last_ai_msg:
                content = ""
                if hasattr(last_ai_msg, "content"):
                    content = last_ai_msg.content
                elif isinstance(last_ai_msg, dict) and "content" in last_ai_msg:
                    content = last_ai_msg["content"]
                
                summary += f"Latest response from agent:\n{content}\n"
            else:
                # Fallback to last message if no AI message found
                msg = messages[-1]
                content = msg.content if hasattr(msg, "content") else str(msg)
                summary += f"Latest message:\n{content}\n"
        else:
             summary += "No messages found in checkpoint.\n"
             
        summary += "\nNote: To get specific progress (steps, %, artifacts), please provide a query_path if the agent writes status to a file."
        
        return format_tool_response("job_status", payload=summary, errors="None")
        
    except Exception as e:
        return format_tool_response("job_status", payload="", errors=f"Error getting job status: {e}")
