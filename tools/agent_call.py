import asyncio
from typing import Optional
from langchain_core.tools import tool
from langchain_core.callbacks import adispatch_custom_event
from core.loaders.agents_loader import AgentsLoader
from core.agent.session_identifier import SessionIdentifier
from core.agent.session_manager import SessionManager
from core.util import format_tool_response
from core.agent.stream_handler import (
    SUBAGENT_STREAM_TOKEN,
    SUBAGENT_STREAM_FINAL,
    EVENT_TOKEN,
    EVENT_FINAL_RESPONSE,
    EVENT_ERROR,
)

async def _safe_dispatch_custom_event(name: str, data: dict):
    """Safely dispatches a custom event to the active stream if running within LangGraph astream_events."""
    try:
        await adispatch_custom_event(name, data)
    except Exception:
        pass

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
            emoji = agent.config.get("emoji", "🤖")
            agent_name = agent.config.get("name", agent_id)
            header = f"{emoji} {agent_name}: "

            header_emitted = False
            accumulated_tokens = []
            subagent_response = None

            async for event in agent.execute_stream(formatted_prompt, session=target_session):
                etype = event.get("type")
                if etype == EVENT_TOKEN:
                    content_delta = event.get("content", "")
                    if content_delta:
                        if not header_emitted:
                            await _safe_dispatch_custom_event(
                                SUBAGENT_STREAM_TOKEN,
                                {"content": header, "agent_id": agent_id, "is_header": True}
                            )
                            header_emitted = True
                        accumulated_tokens.append(content_delta)
                        await _safe_dispatch_custom_event(
                            SUBAGENT_STREAM_TOKEN,
                            {"content": content_delta, "agent_id": agent_id}
                        )
                elif etype == EVENT_FINAL_RESPONSE:
                    subagent_response = event.get("response")
                    await _safe_dispatch_custom_event(
                        SUBAGENT_STREAM_FINAL,
                        {
                            "agent_id": agent_id,
                            "response": subagent_response,
                            "text": event.get("text", "")
                        }
                    )
                elif etype == EVENT_ERROR:
                    err_msg = event.get("content", "Error in subagent execution")
                    if not header_emitted:
                        await _safe_dispatch_custom_event(
                            SUBAGENT_STREAM_TOKEN,
                            {"content": header, "agent_id": agent_id, "is_header": True}
                        )
                        header_emitted = True
                    await _safe_dispatch_custom_event(
                        SUBAGENT_STREAM_TOKEN,
                        {"content": f"\n[Error: {err_msg}]", "agent_id": agent_id}
                    )

            if subagent_response and subagent_response.text:
                full_text = subagent_response.text
            elif accumulated_tokens:
                full_text = "".join(accumulated_tokens)
            else:
                full_text = ""

            return format_tool_response("agent_call", payload=full_text, errors="None")
    except Exception as e:
        return format_tool_response("agent_call", payload="", errors=f"Error calling agent: {e}")
