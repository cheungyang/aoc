import asyncio
from langchain_core.tools import tool
from core.loaders.agents_loader import AgentsLoader
from core.agent.job_manager import JobManager
from core.util import format_tool_response

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
        return format_tool_response("agent_call", payload="", errors="Error: agent_call requires 'agent_id' and 'prompt'.")
    try:
        loader = AgentsLoader()
        agent = loader.get_agent(agent_id)
        job_id = JobManager().new_job_id(agent_id)
        if run_async:
            asyncio.create_task(agent.execute(prompt, source="tool", job_id=job_id))
            return format_tool_response("agent_call", payload=f"Successfully triggered agent '{agent_id}'. Background task started with job_id: {job_id}.", errors="None")
        else:
            response = await agent.execute(prompt, source="tool", job_id=job_id)
            return format_tool_response("agent_call", payload=response, errors="None")
    except Exception as e:
        return format_tool_response("agent_call", payload="", errors=f"Error calling agent: {e}")
