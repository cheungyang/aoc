from langchain_core.tools import tool
from core.loaders.subgraphs_loader import SubgraphsLoader
from core.util import format_tool_response

@tool
async def build_subgraph(subgraph_name: str, query: str) -> str:
    """
    Execute a compiled subgraph by its name with the given query.
    Use this to run specialized multi-agent subgraphs (such as coding orchestration).
    """
    if not subgraph_name or not query:
        return format_tool_response("build_subgraph", payload="", errors="Error: build_subgraph requires 'subgraph_name' and 'query'.")
        
    try:
        loader = SubgraphsLoader()
        subgraph_info = loader.get_subgraph(subgraph_name)
        if not subgraph_info:
            return format_tool_response("build_subgraph", payload="", errors=f"Error: Subgraph '{subgraph_name}' not found.")
            
        graph = subgraph_info["graph"]
        inputs = {"messages": [{"role": "user", "content": query}]}
        
        result = await graph.ainvoke(inputs)
        
        if isinstance(result, dict) and "messages" in result and result["messages"]:
            reply = result["messages"][-1].content
            return format_tool_response("build_subgraph", payload=reply, errors="None")
            
        return format_tool_response("build_subgraph", payload=str(result), errors="None")
    except Exception as e:
        return format_tool_response("build_subgraph", payload="", errors=f"Error executing subgraph: {e}")
