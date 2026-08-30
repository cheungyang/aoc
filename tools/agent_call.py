import asyncio
from typing import Optional
from langchain_core.tools import tool
from core.loaders.agents_loader import AgentsLoader
from core.agent.session_identifier import SessionIdentifier
from core.agent.session_manager import SessionManager
from core.util import format_tool_response

@tool
async def agent_call(
    agent_id: str,
    prompt: str,
    channel: str,
    run_async: bool = False,
    caller: Optional[str] = None
) -> str:
    """
    Consolidated tool for interacting with agents.

    CRITICAL CROSS-CHANNEL ROUTING RULE:
    - Specifying a channel name via the `channel` parameter dispatches and posts messages to that specific Discord channel.
    - If the target `channel` is DIFFERENT from your current conversation channel, you MUST ask and receive explicit user approval BEFORE calling this tool. Never send messages to another channel without user consent.

    Args:
        agent_id: The ID of the target agent to invoke.
        prompt: The prompt or task instructions for the target agent.
        channel: The Discord channel name for routing and permissions. If targeting another channel than the current one, you must obtain user approval first.
        run_async: If True, triggers the agent asynchronously in the background. Defaults to False.
        caller: The ID of the triggering agent (optional, automatically inferred from context if omitted).
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
            
        from core.agent.job_manager import current_session_identifier
        from core.agent.session_manager import SessionManager
        from core.loaders.bots_loader import BotsLoader

        active_sess = current_session_identifier.get()
        if active_sess and active_sess.matches_channel(channel):
            discord_channel = active_sess.channel_obj
        else:
            discord_channel = BotsLoader().find_channel(channel)
        
        triggering_agent = caller or (active_sess.agent_id if active_sess else None)
        if triggering_agent and "<caller>" not in prompt:
            formatted_prompt = f"<caller>{triggering_agent}</caller>\n{prompt}"
        else:
            formatted_prompt = prompt

        is_stateless = agent.config.get("stateless", False)
        target_session = SessionManager().get_session(
            agent_id=agent_id,
            source="tool",
            channel=discord_channel or channel,
            stateless=is_stateless
        )

        if run_async:
            asyncio.create_task(agent.execute(formatted_prompt, session=target_session))
            return format_tool_response("agent_call", payload=f"Successfully triggered agent '{agent_id}'. Background task started with job_id: {target_session.job_id}.", errors="None")
        else:
            response = await agent.execute(formatted_prompt, session=target_session)
            return format_tool_response("agent_call", payload=response, errors="None")
    except Exception as e:
        return format_tool_response("agent_call", payload="", errors=f"Error calling agent: {e}")
