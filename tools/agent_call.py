from langchain_core.tools import tool
from core.loaders.agents_loader import AgentsLoader
from core.util import get_job_id

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
    if action == "launch_subagent":
        if not all([agent_id, prompt]):
            return "Error: 'launch_subagent' requires 'agent_id' and 'prompt'."
        loader = AgentsLoader()
        agent = loader.get_agent(agent_id)
        job_id = get_job_id(agent_id)
        response = await agent.execute(prompt, job_id)
        return response
        
    elif action == "check_subagent":
        return "Action 'check_subagent' is working in progress."
        
    elif action == "update_subagent":
        return "Action 'update_subagent' is working in progress."
        
    elif action == "cancel_subagent":
        return "Action 'cancel_subagent' is working in progress."
        
    else:
        return f"Error: Unknown action '{action}'."
