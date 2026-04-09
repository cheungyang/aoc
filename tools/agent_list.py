from core.agent.agents_loader import AgentsLoader
from langchain_core.tools import tool

@tool
def agent_list() -> list[dict]:
    """
    Returns a list of all agents with an allowlist of attributes: agent_id, name, emoji, description.
    """
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
        
    return result
