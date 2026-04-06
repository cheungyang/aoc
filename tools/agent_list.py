from core.agents_loader import AgentsLoader
from fastmcp.tools import tool

@tool()
def agent_list() -> list[dict]:
    """
    Returns a list of all agents with an allowlist of attributes: agent_id, name, emoji, description.
    """
    loader = AgentsLoader()
    agents = loader.list_agents()
    
    allowed_attributes = ["agent_id", "name", "emoji", "description"]
    result = []
    
    for agent in agents:
        filtered_agent = {}
        for attr in allowed_attributes:
            if attr == "agent_id":
                # Map 'agent_id' or 'id' to 'agent_id'
                filtered_agent["agent_id"] = agent.get("agent_id") or agent.get("id") or ""
            else:
                filtered_agent[attr] = agent.get(attr, "")
        result.append(filtered_agent)
        
    return result
