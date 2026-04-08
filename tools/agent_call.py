from langchain_core.tools import tool
from core.agent.subagent_manager import SubagentManager

@tool
async def agent_call(
    action: str,
    agent_id: str = None,
    prompt: str = None,
    job_id: str = None,
    user_input: str = None
) -> str:
    """
    Consolidated tool for interacting with subagents.
    
    Supported actions:
    - 'launch_subagent': Spawns a subagent asynchronously. Requires 'agent_id', 'prompt'.
    - 'check_subagent': Checks status and output of a subagent task. Requires 'job_id'.
    - 'update_subagent': Passes user input to a subagent waiting for it. Requires 'job_id', 'user_input'.
    - 'cancel_subagent': Terminates a running subagent task. Requires 'job_id'.
    """
    manager = SubagentManager()
    
    if action == "launch_subagent":
        if not all([agent_id, prompt]):
            return "Error: 'launch_subagent' requires 'agent_id' and 'prompt'."
        job_id = manager.launch_task(agent_id, prompt)
        return f"Task launched with job_id: {job_id}"
        
    elif action == "check_subagent":
        if not job_id:
            return "Error: 'check_subagent' requires 'job_id'."
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
        
    elif action == "update_subagent":
        if not all([job_id, user_input]):
            return "Error: 'update_subagent' requires 'job_id' and 'user_input'."
        result = manager.update_task(job_id, user_input)
        return f"Update status: {result}"
        
    elif action == "cancel_subagent":
        if not job_id:
            return "Error: 'cancel_subagent' requires 'job_id'."
        result = manager.cancel_task(job_id)
        return f"Cancel status: {result}"
        
    else:
        return f"Error: Unknown action '{action}'."
