import os
from core.runners.hot_reloader import HotReloader

class SkillsLoader:
    _instance = None

    def __new__(cls, skills_dir="skills"):
        if cls._instance is None:
            cls._instance = super(SkillsLoader, cls).__new__(cls)
            cls._instance.skills_dir = skills_dir
            cls._instance._skills_cache = {}
            HotReloader().start()
        return cls._instance

    def __init__(self, skills_dir="skills"):
        pass

    def _load_skills(self, allowed_skills=None):
        import json
        if os.path.isdir(self.skills_dir):
            for skill_name in os.listdir(self.skills_dir):
                if allowed_skills is not None and skill_name not in allowed_skills:
                    continue
                if skill_name in self._skills_cache:
                    continue
                
                skill_path = os.path.join(self.skills_dir, skill_name, "skill.json")
                if os.path.isfile(skill_path):
                    try:
                        with open(skill_path, "r") as f:
                            config = json.load(f)
                        config["path"] = os.path.join(self.skills_dir, skill_name, "SKILL.md")
                        self._skills_cache[skill_name] = config
                        HotReloader().watch(skill_path, self._on_skill_changed)
                    except Exception as e:
                        print(f"Error loading skill.json for {skill_name}: {e}")

    def get_skill_tools(self, skill_id: str):
        self._load_skills()
        info = self._skills_cache.get(skill_id)
        if not info:
            return {}
        return info.get("tools", {})

    def get_skills_overview(self, agent_id: str):
        from core.loaders.agents_loader import AgentsLoader
        agent = AgentsLoader().get_agent(agent_id)
        allowed_skills = agent.config.get("skills", [])
        
        self._load_skills(allowed_skills)
        overview = f"<skills_list>\nThe following lists the names and descriptions of the skills \n\
            that you have access to. To use a skill, use the `load_skill` tool with the \n\
            skill name to load the skill into your memory. Your agent_id is '{agent_id}'.\n"

        for skill_name, info in self._skills_cache.items():
            if skill_name not in allowed_skills:
                continue
            name = info.get("name")
            desc = info.get("description")
            overview += f"- {name}: {desc}\n"
        overview += "</skills_list>"
        return overview

    def get_skill_prompt(self, agent_id: str, skill_name: str):
        from core.loaders.agents_loader import AgentsLoader
        agent = AgentsLoader().get_agent(agent_id)
        allowed_skills = agent.config.get("skills", [])
        
        if skill_name not in allowed_skills:
            return f"Error: Agent {agent_id} does not have access to skill {skill_name}."
            
        self._load_skills(allowed_skills)
        
        info = self._skills_cache.get(skill_name)
        if not info:
            return f"Skill {skill_name} not found."
            
        skill_path = info.get("path")
        if not skill_path or not os.path.isfile(skill_path):
            return f"Skill file for {skill_name} not found."
            
        with open(skill_path, "r") as f:
            content = f.read()
            
        return f"<skill>\n{content}\n</skill>"

    def clear_skills_cache(self):
        self._skills_cache.clear()

    def _on_skill_changed(self, file_path):
        print(f"SkillsLoader: skill configuration updated at {file_path}, voiding cache.")
        self.clear_skills_cache()
        from core.loaders.tools_loader import ToolsLoader
        ToolsLoader().clear_permissions_cache()

