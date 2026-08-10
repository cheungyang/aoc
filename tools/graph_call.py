from typing import Optional
from langchain_core.tools import tool
from core.loaders.graphs_loader import GraphsLoader
from core.agent.job_manager import current_agent_id
from core.util import format_tool_response

@tool
async def graph_call(graph_name: str = None, query: str = "", caller: Optional[str] = None, subgraph_name: Optional[str] = None) -> str:
    """
    Execute a compiled graph by its name with the given query.
    Use this to run specialized multi-agent graphs (such as coding orchestration).

    Args:
        graph_name: Name of the graph to execute.
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
            
        graph = graph_info["graph"]
        
        triggering_agent = caller or current_agent_id.get()
        if triggering_agent and "<caller>" not in query:
            formatted_query = f"<caller>{triggering_agent}</caller>\n{query}"
        else:
            formatted_query = query

        inputs = {"messages": [{"role": "user", "content": formatted_query}], "query": formatted_query}
        
        tags = ["graph", target_graph]
        metadata = {
            "graph_name": target_graph,
        }
        if triggering_agent:
            tags.append(f"caller:{triggering_agent}")
            metadata["caller"] = triggering_agent
            metadata["triggering_agent"] = triggering_agent
            
        config = {
            "run_name": f"graph:{target_graph}",
            "tags": tags,
            "metadata": metadata
        }
        result = await graph.ainvoke(inputs, config=config)
        
        if isinstance(result, dict) and "messages" in result and result["messages"]:
            reply = result["messages"][-1].content
            return format_tool_response("graph_call", payload=reply, errors="None")
            
        return format_tool_response("graph_call", payload=str(result), errors="None")
    except Exception as e:
        return format_tool_response("graph_call", payload="", errors=f"Error executing graph: {e}")

# Backward compatibility alias
build_subgraph = graph_call
