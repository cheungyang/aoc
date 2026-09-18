from langchain_core.tools import tool
from core.loaders.skills_loader import SkillsLoader
from core.util import format_tool_response
from core.runtime.execution_context import try_context

@tool
def load_skill(skill_id: str) -> str:
    """
    Load the full instructions for a specific skill.
    Use this when you need to perform a skill listed in the skills overview.
    Permissions are derived from the current execution context.
    """
    ctx = try_context()
    if ctx is None:
        return format_tool_response("load_skill", payload="", errors="Error: no active execution context; this tool must be called from an agent run.")
    try:
        loader = SkillsLoader()
        prompt = loader.get_skill_prompt(ctx, skill_id)
        return format_tool_response("load_skill", payload=prompt, errors="None")
    except Exception as e:
        return format_tool_response("load_skill", payload="", errors=f"Error loading skill: {e}")

