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
    """
    if action == "launch_subagent":
        if not all([agent_id, prompt]):
            return "Error: 'launch_subagent' requires 'agent_id' and 'prompt'."
        try:
            loader = AgentsLoader()
            agent = loader.get_agent(agent_id)
            job_id = get_job_id(agent_id)
            response = await agent.execute(prompt, job_id)
            return response
        except Exception as e:
            return f"Error launching subagent: {e}"

    else:
        return f"Error: Unknown action '{action}'."
