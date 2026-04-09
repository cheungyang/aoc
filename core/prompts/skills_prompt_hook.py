def skill_prompt_hook(current_prompt, **kwargs):
    config = kwargs.get("config", {})
    allowed_skills = config.get("skills", [])
    
    from core.loaders.skills_loader import SkillsLoader
    skills_loader = SkillsLoader()
    skills_instructions = skills_loader.get_skill_prompt(allowed_skills=allowed_skills)
    
    updated_prompt = f"\nFollowing are instructions for available skills:\n{skills_instructions}\n\n" + current_prompt
    return updated_prompt

def register_hooks():
    return {
        "system_prompt": skill_prompt_hook
    }
