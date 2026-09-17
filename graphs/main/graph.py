"""The orchestrator's graph: decide first, reason only if needed.

Previously this was `create_react_agent` and nothing else, so every message in
every channel cost at least one model call on the orchestrator -- including the
ones whose answer was fixed by configuration. The graph now opens with a
deterministic `route` step and only falls through to the ReAct agent when the
routing question has more than one possible answer.

Two things to know before editing:

1. This graph is the default for *every* agent (`graph_builder` falls back to
   "main" and no agent.json overrides it). The router is therefore inert unless
   the executing agent both holds `agent_call` and hosts the channel -- see
   `router.routing_enabled`. Any change here must keep the specialist agents on
   the concierge path.
2. The checkpointer belongs to this outer graph. The inner ReAct agent is
   compiled without one, or the two would write the same thread from two levels.
"""
import sys
from typing import Any, Dict, Optional

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import create_react_agent

from core.agent.delegation import agent_header, prompt_to_text, stream_delegate
from graphs.main.router import CONCIERGE, DIRECT_CALL, decide


def _record_turn(session, user_prompt, reply_text: str):
    """Writes a deterministic turn into the human-readable session log.

    The checkpoint takes care of itself -- the node returns an AIMessage and the
    compiled graph persists it. `SqliteSessionStore` does not: it is populated by
    `LoggingHandler.on_llm_start`/`on_llm_end`, which are *LLM* callbacks. A
    routed turn never calls a model, so without this the transcript would simply
    skip every deterministic exchange, and the orchestrator would later read a
    channel's history as though nothing had happened in it.
    """
    try:
        from core.knowledge.memory.sqlite_session_store import SqliteSessionStore

        store = SqliteSessionStore()
        store.append_message(session.session_id, "user", prompt_to_text(user_prompt))
        if reply_text:
            store.append_message(session.session_id, "ai", reply_text)
    except Exception as e:
        print(f"[graphs.main] Warning: could not record routed turn: {e}", file=sys.stderr)


def create_graph(llm, tools, prompt=None, checkpointer=None, agent_id=None, config=None, **kwargs):
    """Compiles the router in front of the conversational ReAct agent."""
    agent_config = config or {}
    owner_agent_id = agent_id or agent_config.get("agent_id") or agent_config.get("id") or ""

    concierge_agent = create_react_agent(llm, tools, prompt=prompt)

    def _decide(state: MessagesState) -> Dict[str, Any]:
        """Resolves the route and stashes it for the conditional edge."""
        from core.agent.execution_context import try_context
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
        )

        update: Dict[str, Any] = {
            "route": decision.target,
            "route_agent_id": decision.agent_id,
            "route_reason": decision.reason,
        }

        # An escape prefix is consumed here rather than downstream, so the
        # concierge is handed the request and not the routing syntax.
        if decision.target == CONCIERGE and decision.prompt != latest and messages:
            trimmed = list(messages)
            trimmed[-1] = trimmed[-1].model_copy(update={"content": decision.prompt})
            update["messages"] = trimmed

        if decision.is_direct:
            print(f"[graphs.main] Routing to '{decision.agent_id}' ({decision.reason})")

        return update

    async def _direct_call(state: Dict[str, Any]) -> Dict[str, Any]:
        """Hands the turn to the one agent allowed to answer here."""
        from core.agent.execution_context import try_context

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

    async def _concierge(state: MessagesState, config_: Optional[dict] = None) -> Dict[str, Any]:
        """The existing ReAct behaviour, for turns with a real decision in them."""
        existing = state.get("messages") or []
        result = await concierge_agent.ainvoke({"messages": existing}, config_)
        produced = (result.get("messages") or [])[len(existing):]
        return {"messages": produced}

    class RouterState(MessagesState):
        route: str
        route_agent_id: Optional[str]
        route_reason: str

    workflow = StateGraph(RouterState)
    workflow.add_node("route", _decide)
    workflow.add_node(DIRECT_CALL, _direct_call)
    workflow.add_node(CONCIERGE, _concierge)

    workflow.add_edge(START, "route")
    workflow.add_conditional_edges(
        "route",
        lambda state: state.get("route") or CONCIERGE,
        {DIRECT_CALL: DIRECT_CALL, CONCIERGE: CONCIERGE},
    )
    workflow.add_edge(DIRECT_CALL, END)
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
