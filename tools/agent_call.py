import asyncio
from langchain_core.tools import tool
from core.loaders.agents_loader import AgentsLoader
from core.agent.job_manager import JobManager
from core.util import format_tool_response

@tool
async def agent_call(
    agent_id: str,
    prompt: str,
    channel: str,
    run_async: bool = False
) -> str:
    """
    Consolidated tool for interacting with agents.
    """
    if not agent_id or not prompt or not channel:
        return format_tool_response("agent_call", payload="", errors="Error: agent_call requires 'agent_id', 'prompt', and 'channel'.")
    try:
        loader = AgentsLoader()
        agent = loader.get_agent(agent_id)
        
        # Check channel permissions
        allowed_channels = agent.config.get("channels", [])
        allowed_channels_lower = [c.lower() for c in allowed_channels]
        if "*" not in allowed_channels_lower and channel.lower() not in allowed_channels_lower:
            return format_tool_response(
                "agent_call",
                payload="",
                errors=f"Error: Agent '{agent_id}' cannot be called in channel '{channel}'. Allowed channels: {allowed_channels}"
            )
            
        from core.loaders.bots_loader import BotsLoader
        discord_channel = BotsLoader().find_channel(channel)
        
        job_id = JobManager().new_job_id(agent_id)
        if run_async:
            asyncio.create_task(agent.execute(prompt, source="tool", job_id=job_id, channel=discord_channel))
            return format_tool_response("agent_call", payload=f"Successfully triggered agent '{agent_id}'. Background task started with job_id: {job_id}.", errors="None")
        else:
            response = await agent.execute(prompt, source="tool", job_id=job_id, channel=discord_channel)
            return format_tool_response("agent_call", payload=response, errors="None")
    except Exception as e:
        return format_tool_response("agent_call", payload="", errors=f"Error calling agent: {e}")
