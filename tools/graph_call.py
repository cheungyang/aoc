import asyncio
import inspect
from typing import Optional
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from core.loaders.graphs_loader import GraphsLoader
from core.agent.job_manager import (
    current_session_identifier,
    current_graph_id,
)
from core.util import format_tool_response

@tool
async def graph_call(graph_name: str = None, query: str = "", caller: Optional[str] = None, subgraph_name: Optional[str] = None) -> str:
    """
    Execute a compiled graph by its name with the given query.
    Use this to run specialized multi-agent graphs (such as coding orchestration or content_creation).
    Note: For all graphs, you must provide project_path or output_dir to initialize the flow.

    Args:
        graph_name: Name of the graph to execute (e.g. 'content_creation', 'coding').
        query: The prompt or task query for the graph.
        caller: The ID of the triggering agent (optional, automatically inferred from context if omitted).
    """
    target_graph = graph_name or subgraph_name
    if not target_graph or not query:
        return format_tool_response("graph_call", payload="", errors="Error: graph_call requires 'graph_name' and 'query'.")
        
    try:
        loader = GraphsLoader()
        graph_info = loader.get_graph(target_graph)
        if not graph_info:
            return format_tool_response("graph_call", payload="", errors=f"Error: Graph '{target_graph}' not found.")
            
        graph = graph_info.get("graph")
        if graph is None and graph_info.get("create_graph") is not None:
            graph = graph_info["create_graph"]()
            
        active_sess = current_session_identifier.get()
        triggering_agent = caller or (active_sess.agent_id if active_sess else None)
        
        # 1. Adapt input
        prepare_input_fn = graph_info.get("prepare_input")
        if prepare_input_fn is not None:
            inputs = prepare_input_fn(query, caller=triggering_agent)
        else:
            if triggering_agent and "<caller>" not in query:
                formatted_query = f"<caller>{triggering_agent}</caller>\n{query}"
            else:
                formatted_query = query
            inputs = {"messages": [{"role": "user", "content": formatted_query}], "query": formatted_query}
        
        if isinstance(inputs, dict) and inputs.get("thread_id"):
            thread_id = inputs["thread_id"]
        elif active_sess:
            thread_id = active_sess.get_session_thread_id(target_graph)
        else:
            thread_id = f"{target_graph}:default"

        tags = ["graph", target_graph]
        metadata = {
            "graph_name": target_graph,
            "thread_id": thread_id,
        }
        if triggering_agent:
            tags.append(f"caller:{triggering_agent}")
            metadata["caller"] = triggering_agent
            metadata["triggering_agent"] = triggering_agent
            
        config = {
            "configurable": {"thread_id": thread_id},
            "run_name": f"graph:{target_graph}",
            "tags": tags,
            "metadata": metadata
        }

        # Check if the thread is currently paused at an interrupt
        is_interrupted = False
        if hasattr(graph, "get_state"):
            try:
                state_snapshot = graph.get_state(config)
                if state_snapshot and getattr(state_snapshot, "next", None):
                    next_nodes = state_snapshot.next
                    if isinstance(next_nodes, (tuple, list)) and len(next_nodes) > 0:
                        is_interrupted = True
            except Exception:
                is_interrupted = False

        graph_token = current_graph_id.set(target_graph)
        try:
            if is_interrupted:
                # Resuming an existing thread from interrupt with user feedback
                update_payload = {
                    "latest_human_feedback": query,
                    "query": query,
                    "messages": [HumanMessage(content=query)]
                }
                
                # Determine as_node to preserve execution progression without resetting next to __start__
                as_node = None
                if hasattr(graph, "get_graph"):
                    try:
                        drawable = graph.get_graph()
                        snap_curr = graph.get_state(config)
                        next_targets = getattr(snap_curr, "next", ())
                        if next_targets and len(next_targets) > 0:
                            tgt = next_targets[0]
                            for edge in drawable.edges:
                                if edge.target == tgt and edge.source != "__start__":
                                    as_node = edge.source
                                    break
                    except Exception:
                        pass

                kwargs_up = {"as_node": as_node} if as_node else {}
                if hasattr(graph, "aupdate_state") and callable(getattr(graph, "aupdate_state")):
                    res_up = graph.aupdate_state(config, update_payload, **kwargs_up)
                    if inspect.isawaitable(res_up):
                        await res_up
                elif hasattr(graph, "update_state") and callable(getattr(graph, "update_state")):
                    graph.update_state(config, update_payload, **kwargs_up)
                result = await graph.ainvoke(None, config=config)
            else:
                result = await graph.ainvoke(inputs, config=config)
        finally:
            current_graph_id.reset(graph_token)
        
        # 2. Adapt output
        format_output_fn = graph_info.get("format_output")
        if format_output_fn is not None:
            reply = format_output_fn(result)
        elif isinstance(result, dict) and "messages" in result and result["messages"]:
            last_msg = result["messages"][-1]
            if hasattr(last_msg, "content"):
                reply = last_msg.content
            elif isinstance(last_msg, dict) and "content" in last_msg:
                reply = last_msg.get("content", str(last_msg))
            else:
                reply = str(last_msg)
        else:
            reply = str(result)
            
        return format_tool_response("graph_call", payload=reply, errors="None")
    except Exception as e:
        return format_tool_response("graph_call", payload="", errors=f"Error executing graph: {e}")
