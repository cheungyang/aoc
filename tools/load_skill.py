from langchain_core.tools import tool
from core.loaders.skills_loader import SkillsLoader
from core.util import format_tool_response

@tool
def load_skill(skill_id: str, agent_id: str) -> str:
    """
    Load the full instructions for a specific skill.
    Use this when you need to perform a skill listed in the skills overview.
    Requires agent_id to verify permissions.
    """
    try:
        loader = SkillsLoader()
        prompt = loader.get_skill_prompt(agent_id, skill_id)
        return format_tool_response("load_skill", payload=prompt, errors="None")
    except Exception as e:
        return format_tool_response("load_skill", payload="", errors=f"Error loading skill: {e}")

