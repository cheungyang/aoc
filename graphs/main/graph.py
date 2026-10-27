"""The orchestrator's graph: decide first, reason only if needed.

Previously this was `create_react_agent` and nothing else, so every message in
every channel cost at least one model call on the orchestrator -- including the
ones whose answer was fixed by configuration. The graph now opens with a
deterministic `route` step. A channel's one agent gets the turn with no model
call on the host (`direct_call`); a message in a channel no single agent answers
gets a fixed line, with no model call at all (`fixed`); a message that calls
another agent by name goes to that agent in its own channel, with a capsule of
the conversation it came from (`handoff`); and the ReAct agent runs only for
turns that are the executing agent's own (`concierge`).

Two things to know before editing:

1. This graph is the default for *every* agent (`GraphBuilder` falls back to
   "main" and no agent.json overrides it). The router is therefore inert unless
   the turn arrived in a channel the executing agent hosts and doesn't answer
   in itself; a delegation, a job or a scheduled run is never routed -- see
   `routing.routing_enabled`. Any change here must keep the specialist agents
   on the concierge path.
2. The checkpointer belongs to this outer graph. The inner ReAct agent is
   compiled without one, or the two would write the same thread from two levels.
"""
import sys
import time
from typing import Any, Dict, Optional

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import create_react_agent

from core.runtime.delegation import agent_header, prompt_to_text, safe_dispatch_custom_event, stream_delegate
from core.runtime.foundation import events
from core.runtime.foundation.addressing import message_text
from core.runtime.foundation.routing import CONCIERGE, DIRECT_CALL, FIXED, HANDOFF, decide
from core.runtime.history.handoff import capsule_for, with_capsule

# What the `fixed` node answers: a message in a channel no single agent answers
# (#general). It offers starting with an agent's name (a hand-off). Typed, it
# also points at the agents' own channels and the commands. Spoken, it gives a
# whole example: TTS reads "…" as a pause.
UNADDRESSED_TEXT = ("No agent picks up here. Start with an agent's name (\"Daisy, …\"), "
                    "post in their channel, or type `!help`.")
UNADDRESSED_SPEECH = "Who's it for? Start with their name, like 'Daisy, what's on today?'"


def _record_turn(session, user_prompt, reply_text: str):
    """Writes a deterministic turn into the human-readable session log.

    The checkpoint takes care of itself -- the node returns an AIMessage and the
    compiled graph persists it. `SessionStore` does not: it is populated by
    `LoggingHandler.on_llm_start`/`on_llm_end`, which are *LLM* callbacks. A
    routed turn never calls a model, so without this the transcript would simply
    skip every deterministic exchange, and the orchestrator would later read a
    channel's history as though nothing had happened in it.
    """
    try:
        from core.storage.sessions import SessionStore

        store = SessionStore()
        store.append_message(session.session_id, "user", prompt_to_text(user_prompt))
        if reply_text:
            store.append_message(session.session_id, "ai", reply_text)
    except Exception as e:
        print(f"[graphs.main] Warning: could not record routed turn: {e}", file=sys.stderr)


def fixed_reply(surface: str) -> str:
    """The fixed line, as `surface` needs it.

    A voice turn gets only a <speech> block: the voice speaks it, and the
    channel shows it too, since it is the whole reply (DiscordStreamBuffer.finalize).
    """
    if surface == "voice":
        return f"<speech>{UNADDRESSED_SPEECH}</speech>"
    return UNADDRESSED_TEXT


def handoff_pointer(agent_config: dict, agent_id: str, channel: str) -> str:
    """What the channel a hand-off came from shows: "→ 🔬 Ted has this in #topic-research"."""
    return f"→ {agent_config.get('emoji', '🤖')} {agent_config.get('name', agent_id)} has this in #{channel}"


def create_graph(llm, tools, prompt=None, checkpointer=None, agent_id=None, config=None, **kwargs):
    """Compiles the router in front of the conversational ReAct agent."""
    agent_config = config or {}
    owner_agent_id = agent_id or agent_config.get("agent_id") or agent_config.get("id") or ""

    concierge_agent = create_react_agent(llm, tools, prompt=prompt)

    def _decide(state: MessagesState) -> Dict[str, Any]:
        """Resolves the route and stashes it for the conditional edge."""
        from core.runtime.foundation.context import try_context
        from core.loaders.agents_loader import AgentsLoader

        ctx = try_context()
        messages = state.get("messages") or []
        latest = messages[-1].content if messages else ""

        decision = decide(
            prompt=latest,
            agent_id=(ctx.agent_id if ctx else owner_agent_id),
            channel_name=(ctx.channel_name if ctx else ""),
            agent_config=agent_config,
            loader=AgentsLoader(),
            source=(ctx.source if ctx else ""),
        )

        if decision.is_direct:
            print(f"[graphs.main] Routing to '{decision.agent_id}' ({decision.reason})")
        elif decision.is_handoff:
            print(f"[graphs.main] Handing off to '{decision.agent_id}' ({decision.reason})")
        elif decision.target == FIXED:
            print(f"[graphs.main] Fixed line ({decision.reason})")

        return {
            "route": decision.target,
            "route_agent_id": decision.agent_id,
            "route_reason": decision.reason,
            "route_channel": decision.channel,
        }

    async def _direct_call(state: Dict[str, Any]) -> Dict[str, Any]:
        """Hands the turn to the one agent allowed to answer here."""
        from core.runtime.foundation.context import try_context

        ctx = try_context()
        messages = state.get("messages") or []
        user_prompt = messages[-1].content if messages else ""
        target = state.get("route_agent_id")

        result = await stream_delegate(
            agent_id=target,
            prompt=user_prompt,
            channel=(ctx.channel_name if ctx else ""),
            caller=(ctx.agent_id if ctx else owner_agent_id),
        )

        if not result.ok:
            # Surfaced as the turn's reply rather than raised: a misconfigured
            # channel should say so in the channel, not crash the graph.
            print(f"[graphs.main] Delegation to '{target}' failed: {result.error}", file=sys.stderr)
            return {"messages": [AIMessage(content=f"Could not reach `{target}`: {result.error}")]}

        # Attributed, not bare. This text becomes part of the orchestrator's own
        # message history, and an unattributed reply would later read as
        # something the orchestrator itself said and reasoned its way to.
        from core.loaders.agents_loader import AgentsLoader

        target_config = AgentsLoader().get_agent_config(target) or {}
        attributed = f"{agent_header(target_config, target)}{result.text}" if result.text else ""

        if ctx is not None:
            _record_turn(ctx, user_prompt, attributed)

        return {"messages": [AIMessage(content=attributed or result.text)]}

    async def _fixed(state: Dict[str, Any]) -> Dict[str, Any]:
        """Answers a message no single agent answers with a fixed line. No model runs."""
        from core.runtime.foundation.context import try_context

        ctx = try_context()
        messages = state.get("messages") or []
        user_prompt = messages[-1].content if messages else ""
        reply = fixed_reply(ctx.get_surface() if ctx else "text")

        # Streamed as well as returned: the channel would show the reply from
        # the final state anyway, but the voice speaks only what streams.
        await safe_dispatch_custom_event(
            events.NODE_STREAM_TOKEN,
            {"content": reply, "agent_id": (ctx.agent_id if ctx else owner_agent_id)},
        )

        if ctx is not None:
            _record_turn(ctx, user_prompt, reply)

        return {"messages": [AIMessage(content=reply)]}

    async def _handoff(state: Dict[str, Any]) -> Dict[str, Any]:
        """Hands the message to the agent it names, in that agent's home channel.

        The named agent answers on the session its own channel's messages use,
        so the conversation continues there, and the message carries a capsule
        of the one it came from (core/runtime/history/handoff.py), built
        without a model. The readers of the stream (the Discord runner, voice)
        learn of the move from the HANDOFF event and show the reply in that
        channel; one that doesn't know the event shows it here. This turn's
        own record -- the checkpoint, the host's log -- keeps the user's words
        and the pointer, never the named agent's reply.
        """
        from core.runtime.foundation.context import try_context
        from core.loaders.agents_loader import AgentsLoader

        started = time.perf_counter()
        ctx = try_context()
        messages = state.get("messages") or []
        user_prompt = messages[-1].content if messages else ""
        target, home = state.get("route_agent_id"), state.get("route_channel")
        loader = AgentsLoader()
        target_config = loader.get_agent_config(target) or {}

        source_channel = None
        if ctx is not None:
            source_channel = ctx.channel_obj if ctx.channel_obj is not None else ctx.channel_name
        capsule = capsule_for(source_channel, loader=loader) if source_channel else ""
        pointer = handoff_pointer(target_config, target, home)
        await safe_dispatch_custom_event(events.ROUTE_HANDOFF, {
            "agent_id": target,
            "name": target_config.get("name", target),
            "emoji": target_config.get("emoji", "🤖"),
            "channel": home,
            "pointer": pointer,
            "request": message_text(user_prompt),
        })
        print(f"[graphs.main] Hand-off to '{target}' in #{home} ready in "
              f"{(time.perf_counter() - started) * 1000:.0f} ms, with {len(capsule)} chars of context")

        result = await stream_delegate(
            agent_id=target,
            prompt=with_capsule(user_prompt, capsule),
            channel=home,
            caller=(ctx.agent_id if ctx else owner_agent_id),
        )
        if not result.ok:
            print(f"[graphs.main] Hand-off to '{target}' failed: {result.error}", file=sys.stderr)
            return {"messages": [AIMessage(content=f"Could not hand this to `{target}`: {result.error}")]}

        if ctx is not None:
            _record_turn(ctx, user_prompt, pointer)
        return {"messages": [AIMessage(content=pointer)]}

    async def _concierge(state: MessagesState, config_: Optional[dict] = None) -> Dict[str, Any]:
        """The ReAct behaviour, for turns that are this agent's own."""
        existing = state.get("messages") or []
        result = await concierge_agent.ainvoke({"messages": existing}, config_)
        produced = (result.get("messages") or [])[len(existing):]
        return {"messages": produced}

    class RouterState(MessagesState):
        route: str
        route_agent_id: Optional[str]
        route_reason: str
        route_channel: Optional[str]

    workflow = StateGraph(RouterState)
    workflow.add_node("route", _decide)
    workflow.add_node(DIRECT_CALL, _direct_call)
    workflow.add_node(FIXED, _fixed)
    workflow.add_node(HANDOFF, _handoff)
    workflow.add_node(CONCIERGE, _concierge)

    workflow.add_edge(START, "route")
    workflow.add_conditional_edges(
        "route",
        lambda state: state.get("route") or CONCIERGE,
        {DIRECT_CALL: DIRECT_CALL, FIXED: FIXED, HANDOFF: HANDOFF, CONCIERGE: CONCIERGE},
    )
    workflow.add_edge(DIRECT_CALL, END)
    workflow.add_edge(FIXED, END)
    workflow.add_edge(HANDOFF, END)
    workflow.add_edge(CONCIERGE, END)

    return workflow.compile(checkpointer=checkpointer)


def prepare_input(query: str, caller: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    """Prepares standard message state from query and caller."""
    if caller and "<caller>" not in query:
        formatted_query = f"<caller>{caller}</caller>\n{query}"
    else:
        formatted_query = query
    return {
        "messages": [{"role": "user", "content": formatted_query}]
    }


def format_output(state: Dict[str, Any]) -> str:
    """Extracts final reply text from graph state."""
    if isinstance(state, dict) and "messages" in state and state["messages"]:
        return state["messages"][-1].content
    return str(state)
