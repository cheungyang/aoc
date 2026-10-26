"""LangGraph's event stream, turned into the turn's events (core/runtime/foundation/events.py).

Also where a stream recovers from a corrupt checkpoint, and where the final
response is read back once the stream ends.
"""
import re
import time
import asyncio
from typing import Optional, List, Any, Dict, Callable
from core.channel.response_parser import AgentResponse
from core.runtime.foundation import events
from core.runtime.foundation.context import ExecutionContext


class StreamHandler:
    """
    Encapsulates LangGraph event streaming, normalization,
    subagent event propagation, and corrupt checkpoint recovery.
    """

    @staticmethod
    async def stream_graph_events(
        graph: Any,
        inputs: Dict[str, Any],
        config: Dict[str, Any],
        agent_id: Optional[str] = None
    ):
        """Low-level graph streaming generator yielding normalized token/tool events.

        `agent_id` is the agent that owns this stream. It matters because
        `astream_events` reports every *descendant* run too: an agent reached
        through `agent_call` — or a graph node that calls one — runs inside this
        callback tree, so its model tokens arrive here indistinguishable from
        the owner's own. Streaming them verbatim is what put a worker's raw
        `<worker_handoff>` XML into the channel, once per attempt. A nested run
        carries its own `agent_id` in the event metadata, so it is skipped:
        whatever the caller does with the subagent's result is the caller's to
        report.
        """
        if agent_id is None and config:
            agent_id = (config.get("metadata") or {}).get("agent_id") or (config.get("configurable") or {}).get("agent_id")

        if hasattr(graph, "astream_events"):
            has_subagent_streamed = False
            async for event in graph.astream_events(inputs, config=config, version="v2"):
                kind = event.get("event")
                if kind == "on_chat_model_stream":
                    if has_subagent_streamed:
                        # Suppress top-level orchestrator (e.g. Concierge) post-delegation
                        # tokens to avoid echoing or duplicating subagent response text
                        continue
                    event_agent = (event.get("metadata") or {}).get("agent_id")
                    if agent_id and event_agent and event_agent != agent_id:
                        continue
                    chunk = event.get("data", {}).get("chunk")
                    if chunk and hasattr(chunk, "content"):
                        content_delta = chunk.content
                        if isinstance(content_delta, str) and content_delta:
                            yield {"type": events.TOKEN, "content": content_delta}
                        elif isinstance(content_delta, list):
                            for p in content_delta:
                                if isinstance(p, dict) and p.get("type") == "text":
                                    t = p.get("text", "")
                                    if t:
                                        yield {"type": events.TOKEN, "content": t}
                                elif isinstance(p, str) and p:
                                    yield {"type": events.TOKEN, "content": p}
                elif kind == "on_tool_start":
                    yield {
                        "type": events.TOOL_START,
                        "tool_name": event.get("name"),
                        "tool_args": event.get("data", {}).get("input", {}),
                        "run_id": event.get("run_id")
                    }
                elif kind == "on_tool_end":
                    yield {
                        "type": events.TOOL_END,
                        "tool_name": event.get("name"),
                        "output": event.get("data", {}).get("output"),
                        "run_id": event.get("run_id")
                    }
                elif kind == "on_custom_event":
                    event_name = event.get("name")
                    if event_name == events.SUBAGENT_STREAM_TOKEN:
                        data = event.get("data", {})
                        content_delta = data.get("content", "")
                        if content_delta:
                            has_subagent_streamed = True
                            # `is_header` marks the "🌼 Daisy: " attribution the
                            # delegation path prefixes to a callee's reply, and
                            # `is_error` a callee's failure. The channel shows
                            # both; speech must not read them out.
                            yield {
                                "type": events.TOKEN,
                                "content": content_delta,
                                "agent_id": data.get("agent_id"),
                                "is_header": bool(data.get("is_header")),
                                "is_error": bool(data.get("is_error")),
                            }
                    elif event_name == events.SUBAGENT_STREAM_FINAL:
                        data = event.get("data", {})
                        has_subagent_streamed = True
                        yield {
                            "type": events.SUBAGENT_FINAL,
                            "agent_id": data.get("agent_id"),
                            "response": data.get("response"),
                            "text": data.get("text", "")
                        }
                    elif event_name == events.NODE_STREAM_TOKEN:
                        # A graph node's own text, with no model behind it
                        # (graphs/main's fixed line). It is the owner's words,
                        # so it neither suppresses nor is suppressed by anything.
                        data = event.get("data", {})
                        content_delta = data.get("content", "")
                        if content_delta:
                            yield {
                                "type": events.TOKEN,
                                "content": content_delta,
                                "agent_id": data.get("agent_id"),
                            }
                    elif event_name in (events.ROUTE_REACTION, events.LLM_RETRY_REACTION):
                        # Deliberately does not set `has_subagent_streamed`: this
                        # announces *that* a delegation happened, before any of the
                        # callee's text exists, and must not suppress the owner's
                        # own tokens on its own.
                        data = event.get("data", {})
                        yield {
                            "type": events.REACTION,
                            "agent_id": data.get("agent_id"),
                            "emoji": data.get("emoji")
                        }
                    elif event_name == events.ROUTE_HANDOFF:
                        # Announces, like a reaction: where the reply that
                        # streams next belongs. The readers move it there.
                        data = event.get("data", {})
                        yield {
                            "type": events.HANDOFF,
                            "agent_id": data.get("agent_id"),
                            "name": data.get("name"),
                            "emoji": data.get("emoji"),
                            "channel": data.get("channel"),
                            "pointer": data.get("pointer", ""),
                            "request": data.get("request", ""),
                        }
        else:
            result = await graph.ainvoke(inputs, config=config)
            reply_message = result["messages"][-1]
            yield {"type": events.TOKEN, "content": str(reply_message.content)}

    @classmethod
    async def stream_with_recovery(
        cls,
        graph: Any,
        inputs: Dict[str, Any],
        config: Dict[str, Any],
        session: ExecutionContext,
        recover_checkpoint_fn: Callable[[ExecutionContext], None],
        is_corrupt_checkpoint_fn: Callable[[Exception], bool],
        agent_id: Optional[str] = None
    ):
        """Streams graph events with automatic corrupt checkpoint recovery and retry."""
        try:
            async for event in cls.stream_graph_events(graph, inputs, config, agent_id=agent_id):
                yield event
        except Exception as e:
            if is_corrupt_checkpoint_fn(e):
                recover_checkpoint_fn(session)
                async for event in cls.stream_graph_events(graph, inputs, config, agent_id=agent_id):
                    yield event
            else:
                raise e

    @staticmethod
    def resolve_final_response(
        graph: Any,
        config: Dict[str, Any],
        accumulated_tokens: Optional[List[str]],
        parse_fn: Callable[[Any], AgentResponse]
    ) -> AgentResponse:
        """Extracts final response from graph state or accumulated stream tokens and parses AgentResponse."""
        state = graph.get_state(config) if hasattr(graph, "get_state") else None
        raw_reply = ""
        if state and hasattr(state, "values") and state.values and "messages" in state.values and state.values["messages"]:
            raw_reply = state.values["messages"][-1].content
        elif accumulated_tokens:
            raw_reply = "".join(accumulated_tokens)
        return parse_fn(raw_reply)
