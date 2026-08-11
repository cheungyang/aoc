from langgraph.prebuilt import create_react_agent
from typing import Optional, Dict, Any

def create_graph(llm, tools, prompt=None, checkpointer=None, **kwargs):
    """Constructs the primary conversational ReAct graph."""
    return create_react_agent(
        llm,
        tools,
        prompt=prompt,
        checkpointer=checkpointer
    )

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
