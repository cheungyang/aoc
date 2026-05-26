import os
<<<<<<< HEAD
=======
import re
>>>>>>> 6b8ec8a8fdc97b58e4988c56fc1189e01e2567c3
from langchain_core.tools import tool
from core.agent.job_manager import JobManager
from core.util import format_tool_response
from core.memory.flat_file_checkpointer import FlatFileCheckpointer
<<<<<<< HEAD

@tool
def job_status(job_id: str, query_path: str = None) -> str:
=======
from tools.agent_call import agent_call

@tool
async def job_status(job_id: str, query_path: str = None) -> str:
>>>>>>> 6b8ec8a8fdc97b58e4988c56fc1189e01e2567c3
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
<<<<<<< HEAD
=======
        content = ""
>>>>>>> 6b8ec8a8fdc97b58e4988c56fc1189e01e2567c3
        
        # If query_path is provided, read from it (Human-in-the-loop path)
        if query_path:
            if os.path.exists(query_path):
                with open(query_path, 'r') as f:
                    content = f.read()
<<<<<<< HEAD
                return format_tool_response("job_status", payload=content, errors="None")
=======
>>>>>>> 6b8ec8a8fdc97b58e4988c56fc1189e01e2567c3
            else:
                return format_tool_response("job_status", payload="", errors=f"Query path {query_path} not found.")
        
        # Fallback: Try to read from checkpointer to get the latest state
<<<<<<< HEAD
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
=======
        if not content:
            checkpointer = FlatFileCheckpointer()
            config = {"configurable": {"thread_id": session_id}}
            checkpoint_tuple = checkpointer.get_tuple(config)
            
            if checkpoint_tuple:
                checkpoint = checkpoint_tuple.checkpoint
                channel_values = checkpoint.get("channel_values", {})
                messages = channel_values.get("messages", [])
                
                if isinstance(messages, list) and len(messages) > 0:
                    # Get last few messages to provide context
                    content = "\n".join([str(m.content if hasattr(m, "content") else m) for m in messages[-5:]])
            
        if not content:
            return format_tool_response("job_status", payload="No status information found. Please provide a query_path.", errors="None")
            
        # Call Skelly agent via agent_call tool to generate the response
        try:
            prompt = f"""
            You are analyzing the status of a background job based on the following information (logs or recent messages).
            Please generate a response as if you were the executing agent reporting your status.
            The response MUST be formatted as XML with the following tags:
            <status>
                <steps>What steps you did</steps>
                <progress_percent>Percentage of progress according to your workflow</progress_percent>
                <artifacts_snippet>Early snippet of completed artifacts (if any)</artifacts_snippet>
            </status>
            
            If the information provided does not contain these details, make your best estimate or state what is missing inside the tags.
            
            Logs/State Information:
            {content}
            """
            
            # agent_call is an async tool, we use ainvoke
            agent_response_str = await agent_call.ainvoke({"agent_id": "skelly", "prompt": prompt})
            
            # Extract payload and errors from Skelly's response
            payload_match = re.search(r'<payload>(.*?)</payload>', agent_response_str, re.DOTALL)
            payload_content = payload_match.group(1) if payload_match else agent_response_str
            
            errors_match = re.search(r'<errors>(.*?)</errors>', agent_response_str, re.DOTALL)
            errors_content = errors_match.group(1) if errors_match else "None"
            
            return format_tool_response("job_status", payload=payload_content, errors=errors_content)
        except Exception as e:
            # Fallback if LLM fails
            summary = f"Job {job_id} status raw content:\n{content}\n\n(Failed to format with LLM: {e})"
            return format_tool_response("job_status", payload=summary, errors="None")
>>>>>>> 6b8ec8a8fdc97b58e4988c56fc1189e01e2567c3
        
    except Exception as e:
        return format_tool_response("job_status", payload="", errors=f"Error getting job status: {e}")
