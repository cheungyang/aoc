"""The single path by which one agent's turn is handed to another.

There are two callers and there must never be three: the `agent_call` tool, used
when the concierge LLM decides to delegate, and the `direct_call` node in
`graphs/main`, used when the router already knows the answer. They differ only in
*who chose the target*. Everything downstream -- the streamed header, the custom
events Discord and voice listen for, the channel permission check, the `<caller>`
prefix -- has to be identical, because the user cannot tell which path ran and
should not be able to.

Keeping both in one function is the point. The duplicate this replaces was the
reason a reaction emoji could appear on one path and not the other.
"""
import asyncio
from dataclasses import dataclass
from typing import Any, List, Optional, Union

from langchain_core.callbacks import adispatch_custom_event

from core.runtime.stream_handler import (
    EVENT_ERROR,
    EVENT_FINAL_RESPONSE,
    EVENT_TOKEN,
    ROUTE_REACTION,
    SUBAGENT_STREAM_FINAL,
    SUBAGENT_STREAM_TOKEN,
)

Prompt = Union[str, list]


@dataclass
class DelegationResult:
    """What the caller needs to report, regardless of which path invoked it."""
    agent_id: str
    text: str = ""
    response: Any = None
    error: Optional[str] = None
    job_id: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


async def safe_dispatch_custom_event(name: str, data: dict):
    """Dispatches to the active stream, or does nothing outside one.

    Delegation is reachable from places that are not inside `astream_events` --
    a scheduled run, a script, a test. Those have no stream to dispatch into, and
    that is not an error.
    """
    try:
        await adispatch_custom_event(name, data)
    except Exception:
        pass


def with_caller(prompt: Prompt, caller: Optional[str]) -> Prompt:
    """Prefixes `<caller>` without flattening a multimodal payload.

    A prompt carrying an image is a list of content parts. Stringifying it to
    attach the caller tag would throw the image away, so the tag is injected as a
    leading text part instead.
    """
    if not caller:
        return prompt

    if isinstance(prompt, str):
        if "<caller>" in prompt:
            return prompt
        return f"<caller>{caller}</caller>\n{prompt}"

    if isinstance(prompt, list):
        for part in prompt:
            if isinstance(part, dict) and part.get("type") == "text" and "<caller>" in (part.get("text") or ""):
                return prompt
        return [{"type": "text", "text": f"<caller>{caller}</caller>\n"}] + list(prompt)

    return prompt


def prompt_to_text(prompt: Prompt) -> str:
    """Best-effort flattening, for logs and job records only -- never for the callee."""
    if isinstance(prompt, str):
        return prompt
    if isinstance(prompt, list):
        texts = []
        for part in prompt:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    texts.append(part.get("text") or "")
                elif part.get("type") == "image_url":
                    texts.append("[image]")
            elif isinstance(part, str):
                texts.append(part)
        return "".join(texts)
    return str(prompt or "")


def channel_permits(agent_config: dict, channel: str) -> bool:
    """Whether `channel` is within the agent's declared `channels` grant."""
    allowed = [c.lower() for c in (agent_config.get("channels") or [])]
    return "*" in allowed or (channel or "").lower() in allowed


def agent_header(agent_config: dict, agent_id: str) -> str:
    """The `emoji Name: ` prefix that opens a delegated reply in the channel."""
    emoji = agent_config.get("emoji", "🤖")
    name = agent_config.get("name", agent_id)
    return f"{emoji} {name}: "


async def stream_delegate(
    agent_id: str,
    prompt: Prompt,
    channel: str,
    caller: Optional[str] = None,
    run_async: bool = False,
    record_memory: bool = True,
    model: Optional[str] = None,
) -> DelegationResult:
    """Runs `agent_id` on `prompt` and streams its reply into the caller's stream.

    Returns a `DelegationResult` rather than raising: both callers need to report
    failure as text in the channel, not as a traceback.

    `record_memory` and `model` are for Python callers only -- the `agent_call`
    tool models see doesn't expose them. `record_memory=False` keeps the callee's
    `<system_memory_log>` from being saved (and is inherited by anything it
    delegates to); `model` overrides the callee's model tier for this call.
    """
    from core.runtime.execution_context import try_context
    from core.runtime.session_manager import SessionManager
    from core.loaders.agents_loader import AgentsLoader
    from core.channel.discord.loader import BotsLoader

    if not agent_id or prompt is None or not channel:
        return DelegationResult(
            agent_id=agent_id or "",
            error="Delegation requires 'agent_id', 'prompt', and 'channel'.",
        )

    try:
        agent = AgentsLoader().get_agent(agent_id)
    except Exception as e:
        return DelegationResult(agent_id=agent_id, error=f"Unknown agent '{agent_id}': {e}")

    config = agent.config

    if not channel_permits(config, channel):
        return DelegationResult(
            agent_id=agent_id,
            error=(
                f"Agent '{agent_id}' cannot be called in channel '{channel}'. "
                f"Allowed channels: {config.get('channels', [])}"
            ),
        )

    active_sess = try_context()
    if active_sess and active_sess.matches_channel(channel):
        discord_channel = active_sess.channel_obj
    else:
        discord_channel = BotsLoader().find_channel(channel)

    triggering_agent = caller or (active_sess.agent_id if active_sess else None)
    formatted_prompt = with_caller(prompt, triggering_agent)

    is_stateless = config.get("stateless", False)
    # Inherit the caller's graph binding: an agent invoked from inside a graph is
    # evaluated against that graph's grants, and that has to travel explicitly.
    target_session = SessionManager().get_session(
        agent_id=agent_id,
        source="tool",
        channel=discord_channel or channel,
        stateless=is_stateless,
        graph_id=active_sess.graph_id if active_sess else None,
        record_memory=record_memory,
        model=model,
    )

    # The reaction is dispatched from here, not from the callers, so the concierge
    # path and the router path acknowledge a delegation identically.
    await safe_dispatch_custom_event(
        ROUTE_REACTION,
        {"agent_id": agent_id, "emoji": config.get("emoji", "🤖")},
    )

    if run_async:
        asyncio.create_task(agent.execute(formatted_prompt, session=target_session))
        return DelegationResult(
            agent_id=agent_id,
            text=(
                f"Successfully triggered agent '{agent_id}'. "
                f"Background task started with job_id: {target_session.job_id}."
            ),
            job_id=target_session.job_id,
        )

    if is_stateless:
        res = await agent.execute(formatted_prompt, session=target_session)
        text = res if isinstance(res, str) else (res.text if hasattr(res, "text") else str(res or ""))
        return DelegationResult(agent_id=agent_id, text=text, job_id=target_session.job_id)

    header = agent_header(config, agent_id)
    header_emitted = False
    accumulated: List[str] = []
    subagent_response = None

    async def _emit_header():
        nonlocal header_emitted
        if not header_emitted:
            await safe_dispatch_custom_event(
                SUBAGENT_STREAM_TOKEN,
                {"content": header, "agent_id": agent_id, "is_header": True},
            )
            header_emitted = True

    async for event in agent.execute_stream(formatted_prompt, session=target_session):
        etype = event.get("type")

        if etype == EVENT_TOKEN:
            delta = event.get("content", "")
            if delta:
                await _emit_header()
                accumulated.append(delta)
                await safe_dispatch_custom_event(
                    SUBAGENT_STREAM_TOKEN,
                    {"content": delta, "agent_id": agent_id},
                )

        elif etype == EVENT_FINAL_RESPONSE:
            subagent_response = event.get("response")
            await safe_dispatch_custom_event(
                SUBAGENT_STREAM_FINAL,
                {
                    "agent_id": agent_id,
                    "response": subagent_response,
                    "text": event.get("text", ""),
                },
            )

        elif etype == EVENT_ERROR:
            err_msg = event.get("content", "Error in subagent execution")
            await _emit_header()
            await safe_dispatch_custom_event(
                SUBAGENT_STREAM_TOKEN,
                {"content": f"\n[Error: {err_msg}]", "agent_id": agent_id},
            )

    if subagent_response is not None and getattr(subagent_response, "text", ""):
        full_text = subagent_response.text
    elif accumulated:
        full_text = "".join(accumulated)
    else:
        full_text = ""

    return DelegationResult(
        agent_id=agent_id,
        text=full_text,
        response=subagent_response,
        job_id=target_session.job_id,
    )
