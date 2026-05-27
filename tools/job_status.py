import os
import asyncio
import re
from langchain_core.tools import tool
from core.agent.job_manager import JobManager
from core.util import format_tool_response
from core.memory.flat_file_session_store import FlatFileSessionStore
from tools.agent_call import agent_call

@tool
async def job_status(job_id: str) -> str:
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
        file_path = session_store.get_file_path(session_id)
        file_exists = os.path.exists(file_path)
        if not file_exists:
            print(f"Session file not found for session: {session_id}")
            
        history = session_store.load_history(session_id, limit=100)
        ai_messages = [entry.get("message", "") for entry in history if entry.get("from") == "ai"]
        ai_message_count = len(ai_messages)
        
        debug_msg = f"File found: {file_exists}, AI messages: {ai_message_count}\n"
        print(debug_msg)
        
        if not history:
            return format_tool_response("job_status", payload=f"{debug_msg}No session history found for session {session_id}.", errors="None")
            
        summary = f"Job {job_id} status (Session: {session_id}):\n"
        summary += debug_msg
        
        # Show AI messages
        summary += "\n--- AI Messages ---\n"
        for i, msg in enumerate(ai_messages, 1):
            summary += f"[{i}] {msg}\n"
        summary += "-------------------\n\n"
        
        # Call skill-runner to compile progress
        ai_messages_text = "\n".join(ai_messages)
        prompt = f"Here are the AI messages from the session history. Please compile the 'steps done, % of progress, and early snippets' requested:\n\n{ai_messages_text}"
        
        try:
            tool_res = await agent_call.ainvoke({"agent_id": "skill-runner", "prompt": prompt})
            
            # Extract payload
            match = re.search(r"<payload>(.*?)</payload>", tool_res, re.DOTALL)
            if match:
                compiled_progress = match.group(1)
            else:
                compiled_progress = tool_res # Fallback
                
            summary += f"Compiled Progress from skill-runner:\n{compiled_progress}\n"
        except Exception as e:
            summary += f"Error calling skill-runner: {e}\n"
            
        return format_tool_response("job_status", payload=summary, errors="None")
        
    except Exception as e:
        return format_tool_response("job_status", payload="", errors=f"Error getting job status: {e}")
