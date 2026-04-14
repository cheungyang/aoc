from langchain_core.tools import tool
from core.loaders.skills_loader import SkillsLoader

@tool
def load_skill(skill_id: str, agent_id: str) -> str:
    """
    Load the full instructions for a specific skill.
    Use this when you need to perform a skill listed in the skills overview.
    Requires agent_id to verify permissions.
    """
    try:
        loader = SkillsLoader()
        return loader.get_skill_prompt(agent_id, skill_id)
    except Exception as e:
        return f"Error loading skill: {e}"

