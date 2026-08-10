from typing import Optional
from langchain_core.tools import tool
from core.loaders.subgraphs_loader import SubgraphsLoader
from core.agent.job_manager import current_agent_id
from core.util import format_tool_response

@tool
async def build_subgraph(subgraph_name: str, query: str, caller: Optional[str] = None) -> str:
    """
    Execute a compiled subgraph by its name with the given query.
    Use this to run specialized multi-agent subgraphs (such as coding orchestration).

    Args:
        subgraph_name: Name of the subgraph to execute.
        query: The prompt or task query for the subgraph.
        caller: The ID of the triggering agent (optional, automatically inferred from context if omitted).
    """
    if not subgraph_name or not query:
        return format_tool_response("build_subgraph", payload="", errors="Error: build_subgraph requires 'subgraph_name' and 'query'.")
        
    try:
        loader = SubgraphsLoader()
        subgraph_info = loader.get_subgraph(subgraph_name)
        if not subgraph_info:
            return format_tool_response("build_subgraph", payload="", errors=f"Error: Subgraph '{subgraph_name}' not found.")
            
        graph = subgraph_info["graph"]
        
        triggering_agent = caller or current_agent_id.get()
        if triggering_agent and "<caller>" not in query:
            formatted_query = f"<caller>{triggering_agent}</caller>\n{query}"
        else:
            formatted_query = query

        inputs = {"messages": [{"role": "user", "content": formatted_query}], "query": formatted_query}
        
        tags = ["subgraph", subgraph_name]
        metadata = {
            "subgraph_name": subgraph_name,
        }
        if triggering_agent:
            tags.append(f"caller:{triggering_agent}")
            metadata["caller"] = triggering_agent
            metadata["triggering_agent"] = triggering_agent
            
        config = {
            "run_name": f"subgraph:{subgraph_name}",
            "tags": tags,
            "metadata": metadata
        }
        result = await graph.ainvoke(inputs, config=config)
        
        if isinstance(result, dict) and "messages" in result and result["messages"]:
            reply = result["messages"][-1].content
            return format_tool_response("build_subgraph", payload=reply, errors="None")
            
        return format_tool_response("build_subgraph", payload=str(result), errors="None")
    except Exception as e:
        return format_tool_response("build_subgraph", payload="", errors=f"Error executing subgraph: {e}")
