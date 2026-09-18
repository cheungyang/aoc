from typing import Optional
from langchain_core.tools import tool
from core.runtime.delegation import stream_delegate
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
        return format_tool_response(
            "agent_call",
            payload="",
            errors="Error: agent_call requires 'agent_id', 'prompt', and 'channel'.",
        )

    # Everything below the argument check is shared with the router's `direct_call`
    # node. This tool's only remaining job is to be the LLM-facing surface: the
    # schema above, and the tool-response envelope below.
    try:
        result = await stream_delegate(
            agent_id=agent_id,
            prompt=prompt,
            channel=channel,
            caller=caller,
            run_async=run_async,
        )
    except Exception as e:
        return format_tool_response("agent_call", payload="", errors=f"Error calling agent: {e}")

    if not result.ok:
        return format_tool_response("agent_call", payload="", errors=f"Error: {result.error}")

    return format_tool_response("agent_call", payload=result.text, errors="None")
