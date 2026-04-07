from fastmcp.tools import tool
from core.subagent_manager import SubagentManager

@tool()
def launch_subagent(agent_id: str, prompt: str, session_id: str) -> str:
    """
    Spawns a subagent asynchronously.
    Returns a job_id to track the task.
    """
    manager = SubagentManager()
    job_id = manager.launch_task(agent_id, prompt, session_id)
    return f"Task launched with job_id: {job_id}"

@tool()
def check_subagent(job_id: str) -> str:
    """
    Checks the status and output of a subagent task.
    """
    manager = SubagentManager()
    result = manager.check_task(job_id)
    
    status = result.get("status")
    res = result.get("result")
    question = result.get("question")
    
    output = f"Status: {status}"
    if res:
        output += f"\nResult: {res}"
    if question:
        output += f"\nQuestion for user: {question}"
        
    return output

@tool()
def update_subagent(job_id: str, user_input: str) -> str:
    """
    Passes user input to a subagent that is waiting for it.
    """
    manager = SubagentManager()
    result = manager.update_task(job_id, user_input)
    return f"Update status: {result}"

@tool()
def cancel_subagent(job_id: str) -> str:
    """
    Terminates a running subagent task.
    """
    manager = SubagentManager()
    result = manager.cancel_task(job_id)
    return f"Cancel status: {result}"
