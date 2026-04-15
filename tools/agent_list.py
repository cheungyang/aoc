from core.loaders.agents_loader import AgentsLoader
from langchain_core.tools import tool
from core.util import format_tool_response

@tool
def agent_list() -> str:
    """
    Returns a list of all agents with an allowlist of attributes: agent_id, name, emoji, description.
    """
    try:
        loader = AgentsLoader()
        agent_ids = loader.list_agent_ids()
        
        allowed_attributes = ["name", "emoji", "description"]
        result = []
        
        for agent_id in agent_ids:
            agent = loader.get_agent(agent_id)
            config = agent.config
            filtered_agent = {"agent_id": agent_id}
            for attr in allowed_attributes:
                filtered_agent[attr] = config.get(attr, "")
            result.append(filtered_agent)
            
        return format_tool_response("agent_list", payload=str(result), errors="None")
    except Exception as e:
        return format_tool_response("agent_list", payload="", errors=f"Error listing agents: {e}")
