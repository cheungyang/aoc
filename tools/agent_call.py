import asyncio
from langchain_core.tools import tool
from core.loaders.agents_loader import AgentsLoader
from core.agent.job_manager import JobManager

@tool
async def agent_call(
    agent_id: str,
    prompt: str,
    run_async: bool = False
) -> str:
    """
    Consolidated tool for interacting with agents.
    """
    if not agent_id or not prompt:
        return "Error: agent_call requires 'agent_id' and 'prompt'."
    try:
        loader = AgentsLoader()
        agent = loader.get_agent(agent_id)
        job_id = JobManager().new_job_id(agent_id)
        if run_async:
            asyncio.create_task(agent.execute(prompt, source="tool", job_id=job_id))
            return f"Successfully triggered agent '{agent_id}'. Background task started with job_id: {job_id}."
        else:
            response = await agent.execute(prompt, source="tool", job_id=job_id)
            return response
    except Exception as e:
        return f"Error calling agent: {e}"
