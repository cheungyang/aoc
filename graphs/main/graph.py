"""The orchestrator's graph: decide first, reason only if needed.

Previously this was `create_react_agent` and nothing else, so every message in
every channel cost at least one model call on the orchestrator -- including the
ones whose answer was fixed by configuration. The graph now opens with a
deterministic `route` step. A channel's one agent gets the turn with no model
call on the host (`direct_call`); a message in a channel the host shares on
purpose (#general) gets a fixed line, with no model call at all (`fixed`); a
message in any other channel without exactly one agent gets a notice that the
channel isn't set up, also without a model (`unmapped`); a message that calls
another agent by name goes to that agent in its own channel, with a capsule of
the conversation it came from (`handoff`); and the ReAct agent runs only for
turns that are the executing agent's own (`concierge`).

An agent's reply can hand you on too, ending with <switch to="...">
(core/prompts/templates/system/team.md). The voice room and the channel follow
it on their own; this graph parks a capsule of the conversation for the
teammate, with the brief the agent wrote inside the tag
(core/storage/handoff_capsules.py), and `direct_call` attaches it to your first
message in the teammate's channel.

On a voice turn, the `route` step also drops the copies of your words that a
turn cancelled when you went on talking left unanswered, since this turn's
message repeats them (core/runtime/history/repeats.py). It runs first in
every history a voice turn runs on: main's for the channel, and the agent's.

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
import asyncio
import sys
import time
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, RemoveMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import create_react_agent

from core.channel.handoff import agent_named
from core.runtime.delegation import agent_header, prompt_to_text, safe_dispatch_custom_event, stream_delegate
from core.runtime.foundation import events
from core.runtime.foundation.addressing import message_text
from core.runtime.foundation.routing import CONCIERGE, DIRECT_CALL, FIXED, HANDOFF, UNMAPPED, decide, home_channel
from core.runtime.history.handoff import SWITCH_CAPSULE_TOKENS, capsule_for, with_capsule
from core.runtime.history.repeats import unanswered_repeats
from core.runtime.observability.turn_trace import current_turn_trace

# What the `fixed` node answers: a message in a channel no single agent answers
# (#general). It offers starting with an agent's name (a hand-off). Typed, it
# also points at the agents' own channels and the commands. Spoken, it gives a
# whole example: TTS reads "…" as a pause.
UNADDRESSED_TEXT = ("No agent picks up here. Start with an agent's name (\"Daisy, …\"), "
                    "post in their channel, or type `!help`.")
UNADDRESSED_SPEECH = "Who's it for? Start with their name, like 'Daisy, what's on today?'"

# What the `unmapped` node answers: a message in a channel that isn't set up --
# no agent lists it, or several do, and it isn't one of the host's
# `shared_channels`. The notice says what to change. Spoken, the voice says the
# short line, and the channel still shows the whole notice.
UNMAPPED_FIX = "or add it to `shared_channels` in agents/main/agent.json."
UNMAPPED_SPEECH = ("This channel isn't set up yet, so nobody answers here. "
                   "Start with an agent's name, like 'Daisy, what's on today?'")

# How a capsule's conversation reached the agent it's for, on its tag
# (`via`) and in the turn ledger (core/proactive/taps.py).
VIA_HANDOFF = "handoff"   # you named the agent: the router's hand-off
VIA_SWITCH = "switch"     # an agent's reply handed you over: <switch to="...">


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


def _source(ctx: Any) -> Any:
    """Where a turn's conversation is read from: its Discord channel or thread, else its name."""
    return ctx.channel_obj if ctx.channel_obj is not None else ctx.channel_name


def _session_id(agent_id: str, channel: Any) -> str:
    """The session `agent_id` answers on in `channel`, as stream_delegate opens it."""
    from core.runtime.foundation.sessions import SessionManager

    return SessionManager.get_session(agent_id, source="tool", channel=channel).session_id


def _park_switch(response: Any, *, agent_id: str, channel: str, source: Any, loader: Any) -> Optional[int]:
    """Parks a capsule for the teammate a reply handed you to (<switch to="...">),
    for its first turn in its own channel; the capsule's id, or None.

    `agent_id` replied in `channel`, read from `source` (its thread, or its
    name). Its run has ended, so the reply is in the transcript the capsule is
    read from: LoggingHandler logs it when the model call ends, before the
    stream does. Nothing is parked for a teammate with no channel of its own,
    for the agent itself, or for one whose channel this is. Never raises: a
    hand-over without context still reaches the teammate.
    """
    wanted = getattr(response, "switch_to", None)
    if not isinstance(wanted, str) or not wanted:
        return None
    try:
        teammate = agent_named(wanted, loader)
        home = home_channel(teammate, loader) if teammate else None
        if not home or teammate == agent_id or home == (channel or "").lower():
            return None
        brief = getattr(response, "switch_brief", None) or ""
        capsule = capsule_for(source, loader=loader, brief=brief, via=VIA_SWITCH, max_tokens=SWITCH_CAPSULE_TOKENS)
        if not capsule:
            return None
        from core.storage.handoff_capsules import HandoffCapsuleStore

        parked = HandoffCapsuleStore().park(from_agent=agent_id, from_channel=channel, to_agent=teammate,
                                            to_channel=home, capsule=capsule, brief=brief, via=VIA_SWITCH)
    except Exception as e:
        print(f"[graphs.main] Warning: could not park a hand-off capsule for '{wanted}': {e}", file=sys.stderr)
        return None
    print(f"[graphs.main] Parked hand-off capsule #{parked} for '{teammate}' in #{home}: {len(capsule)} chars"
          + (", with a brief" if brief else ""))
    return parked


def _take_parked(agent_id: str, channel: str, answers_in: Any) -> Optional[Any]:
    """The capsule an agent's <switch> parked for `agent_id`'s next turn in
    `channel`, claimed for the session it answers on in `answers_in` (the
    channel, or a thread of it): None if there's none, or if it can't be read,
    which only costs the turn its context."""
    try:
        from core.storage.handoff_capsules import HandoffCapsuleStore

        return HandoffCapsuleStore().take(agent_id, channel, session_id=_session_id(agent_id, answers_in))
    except Exception as e:
        print(f"[graphs.main] Warning: could not read parked hand-off capsules for '{agent_id}': {e}",
              file=sys.stderr)
        return None


def _attached(parked: Any) -> str:
    """A taken capsule's text, for the prompt, with the log line that says so."""
    print(f"[graphs.main] Attached parked capsule #{parked.id} from '{parked.from_agent}' "
          f"({len(parked.capsule)} chars)")
    return parked.capsule


def _handoff_capsules(source: Any, source_name: str, parked: Any, loader: Any) -> List[str]:
    """What a router hand-off carries: a capsule of the conversation it came
    from, and the one an agent's <switch> parked for the named agent there, if
    any (`_take_parked`).

    A capsule parked from the channel the hand-off came from is the same
    conversation, read earlier: the hand-off's own capsule stands for both, and
    carries its brief. One parked from another channel goes after it.
    """
    same = parked is not None and parked.from_channel == (source_name or "").lower()
    brief = parked.brief if same else None
    own = capsule_for(source, loader=loader, brief=brief, via=VIA_HANDOFF) if source else ""
    if parked is None:
        return [own]
    if same and own:
        print(f"[graphs.main] Parked capsule #{parked.id} from '{parked.from_agent}' is the conversation this "
              f"hand-off came from: its own capsule carries it" + (", with the brief" if brief else ""))
        return [own]
    return [own, _attached(parked)]


def fixed_reply(surface: str) -> str:
    """The fixed line, as `surface` needs it.

    A voice turn gets only a <speech> block: the voice speaks it, and the
    channel shows it too, since it is the whole reply (DiscordStreamBuffer.finalize).
    """
    if surface == "voice":
        return f"<speech>{UNADDRESSED_SPEECH}</speech>"
    return UNADDRESSED_TEXT


def _and(items: List[str]) -> str:
    """Joins names as a sentence does: `a`, `a and b`, `a, b and c`."""
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def unmapped_reply(channel: str, candidates: Any, surface: str) -> str:
    """The notice that `channel` isn't set up, as `surface` needs it: no agent
    lists it, or `candidates` all do, and what to change either way.

    A voice turn also gets the short <speech> line; the channel shows the notice.
    """
    where = f"#{channel}" if channel else "This channel"
    names = [f"`{agent}`" for agent in (candidates or ())]
    if names:
        why = (f"{_and(names)} {'list' if len(names) > 1 else 'lists'} it, so no single agent answers. "
               f"Keep it in one agent's `channels`, {UNMAPPED_FIX}")
    else:
        why = f"no agent lists it in its `channels`. Give it to one agent, {UNMAPPED_FIX}"
    text = f"⚠️ {where} isn't set up: {why}"
    if surface == "voice":
        return f"<speech>{UNMAPPED_SPEECH}</speech>\n{text}"
    return text


def handoff_pointer(agent_config: dict, agent_id: str, channel: str) -> str:
    """What the channel a hand-off came from shows: "→ 🔬 Ted has this in #topic-research"."""
    return f"→ {agent_config.get('emoji', '🤖')} {agent_config.get('name', agent_id)} has this in #{channel}"


def drop_repeats(messages: List[Any], ctx: Any) -> List[RemoveMessage]:
    """On a voice turn, removes the copies of your words a cancelled turn left
    unanswered right before this turn's message, which repeats them
    (core/runtime/history/repeats.py); nothing on any other turn. Logged, and
    put on the voice trace (`repeats_dropped`)."""
    if ctx is None or ctx.get_surface() != "voice":
        return []
    ids = unanswered_repeats(messages)
    if not ids:
        return []
    print(f"[graphs.main] Dropped {len(ids)} unanswered {'copy' if len(ids) == 1 else 'copies'} of the words "
          f"this turn repeats, from {ctx.agent_id}'s history")
    trace = current_turn_trace()
    if trace is not None:
        trace.event("repeats_dropped", agent=ctx.agent_id, count=len(ids))
    return [RemoveMessage(id=message_id) for message_id in ids]


def create_graph(llm, tools, prompt=None, checkpointer=None, agent_id=None, config=None, model=None, **kwargs):
    """Compiles the router in front of the conversational ReAct agent.

    `model` replaces `llm` in the ReAct step: graphs/voice passes one that
    picks per call, `(state, runtime) -> runnable`, with tools bound by itself.
    """
    agent_config = config or {}
    owner_agent_id = agent_id or agent_config.get("agent_id") or agent_config.get("id") or ""

    concierge_agent = create_react_agent(model or llm, tools, prompt=prompt)

    def _decide(state: MessagesState) -> Dict[str, Any]:
        """Resolves the route and stashes it for the conditional edge. On a
        voice turn, it also drops the copies of your words this turn repeats."""
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
        elif decision.target == UNMAPPED:
            print(f"[graphs.main] ⚠️ Channel not set up ({decision.reason})", file=sys.stderr)

        update: Dict[str, Any] = {
            "route": decision.target,
            "route_agent_id": decision.agent_id,
            "route_reason": decision.reason,
            "route_channel": decision.channel,
            "route_candidates": list(decision.candidates),
        }
        repeats = drop_repeats(messages, ctx)
        if repeats:
            update["messages"] = repeats
        return update

    async def _direct_call(state: Dict[str, Any]) -> Dict[str, Any]:
        """Hands the turn to the one agent allowed to answer here.

        When an agent's <switch> handed you to this one, the capsule it parked
        goes with your first message here, once. When this agent's reply hands
        you on in turn, a capsule is parked for that teammate.
        """
        from core.runtime.foundation.context import try_context
        from core.loaders.agents_loader import AgentsLoader

        ctx = try_context()
        messages = state.get("messages") or []
        user_prompt = messages[-1].content if messages else ""
        target = state.get("route_agent_id")
        channel = ctx.channel_name if ctx else ""

        # Taken before the call. A call fails only before the agent runs (an
        # unknown agent, a channel it may not answer), and the agent a channel
        # routes to does neither.
        parked = _take_parked(target, channel, answers_in=_source(ctx)) if ctx is not None else None
        result = await stream_delegate(
            agent_id=target,
            prompt=with_capsule(user_prompt, _attached(parked) if parked else ""),
            channel=channel,
            caller=(ctx.agent_id if ctx else owner_agent_id),
            routed=True,  # the channel chose it: the agent you're talking to
        )

        if not result.ok:
            # Surfaced as the turn's reply rather than raised: a misconfigured
            # channel should say so in the channel, not crash the graph.
            print(f"[graphs.main] Delegation to '{target}' failed: {result.error}", file=sys.stderr)
            return {"messages": [AIMessage(content=f"Could not reach `{target}`: {result.error}")]}

        # Attributed, not bare. This text becomes part of the orchestrator's own
        # message history, and an unattributed reply would later read as
        # something the orchestrator itself said and reasoned its way to.
        loader = AgentsLoader()
        target_config = loader.get_agent_config(target) or {}
        attributed = f"{agent_header(target_config, target)}{result.text}" if result.text else ""

        if ctx is not None:
            _record_turn(ctx, user_prompt, attributed)
            # Off the event loop: the reply's audio may still be streaming.
            await asyncio.to_thread(_park_switch, result.response, agent_id=target, channel=channel,
                                    source=_source(ctx), loader=loader)

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

    async def _unmapped(state: Dict[str, Any]) -> Dict[str, Any]:
        """Says the channel isn't set up: no agent lists it, or several do, and
        it isn't shared. No model runs; the notice says what to change."""
        from core.runtime.foundation.context import try_context

        ctx = try_context()
        messages = state.get("messages") or []
        user_prompt = messages[-1].content if messages else ""
        reply = unmapped_reply(ctx.channel_name if ctx else "", state.get("route_candidates") or (),
                               ctx.get_surface() if ctx else "text")

        # Streamed as well as returned, as the fixed line is: the voice speaks only what streams.
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
        without a model -- and the capsule an agent's <switch> parked for it
        there, if one waits (`_handoff_capsules`). The readers of the stream
        (the Discord runner, voice) learn of the move from the HANDOFF event and
        show the reply in that channel; one that doesn't know the event shows it
        here. This turn's own record -- the checkpoint, the host's log -- keeps
        the user's words and the pointer, never the named agent's reply. If the
        reply hands you on, a capsule is parked for that teammate.
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

        source_channel = _source(ctx) if ctx is not None else None
        parked = _take_parked(target, home, answers_in=home) if home else None
        capsules = _handoff_capsules(source_channel, ctx.channel_name if ctx else "", parked, loader)
        prompt = user_prompt
        for capsule in capsules:
            prompt = with_capsule(prompt, capsule)
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
              f"{(time.perf_counter() - started) * 1000:.0f} ms, "
              f"with {sum(len(capsule) for capsule in capsules)} chars of context")

        result = await stream_delegate(
            agent_id=target,
            prompt=prompt,
            channel=home,
            caller=(ctx.agent_id if ctx else owner_agent_id),
            routed=True,  # you named it: the agent you're talking to
        )
        if not result.ok:
            print(f"[graphs.main] Hand-off to '{target}' failed: {result.error}", file=sys.stderr)
            return {"messages": [AIMessage(content=f"Could not hand this to `{target}`: {result.error}")]}

        if ctx is not None:
            _record_turn(ctx, user_prompt, pointer)
        await asyncio.to_thread(_park_switch, result.response, agent_id=target, channel=home, source=home,
                                loader=loader)
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
        route_candidates: List[str]

    workflow = StateGraph(RouterState)
    workflow.add_node("route", _decide)
    workflow.add_node(DIRECT_CALL, _direct_call)
    workflow.add_node(FIXED, _fixed)
    workflow.add_node(UNMAPPED, _unmapped)
    workflow.add_node(HANDOFF, _handoff)
    workflow.add_node(CONCIERGE, _concierge)

    workflow.add_edge(START, "route")
    workflow.add_conditional_edges(
        "route",
        lambda state: state.get("route") or CONCIERGE,
        {DIRECT_CALL: DIRECT_CALL, FIXED: FIXED, UNMAPPED: UNMAPPED, HANDOFF: HANDOFF, CONCIERGE: CONCIERGE},
    )
    workflow.add_edge(DIRECT_CALL, END)
    workflow.add_edge(FIXED, END)
    workflow.add_edge(UNMAPPED, END)
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
