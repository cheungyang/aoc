import os

class SkillsLoader:
    _instance = None

    def __new__(cls, skills_dir="skills"):
        if cls._instance is None:
            cls._instance = super(SkillsLoader, cls).__new__(cls)
            cls._instance.skills_dir = skills_dir
            cls._instance.skills_cache = {}
        return cls._instance

    def __init__(self, skills_dir="skills"):
        pass

    def _load_skills(self, allowed_skills=None):
        if os.path.isdir(self.skills_dir):
            for skill_name in os.listdir(self.skills_dir):
                if allowed_skills is not None and skill_name not in allowed_skills:
                    continue
                if skill_name in self.skills_cache:
                    continue
                
                skill_path = os.path.join(self.skills_dir, skill_name, "SKILL.md")
                if os.path.isfile(skill_path):
                    with open(skill_path, "r") as f:
                        content = f.read()
                    parts = content.split("---")
                    if len(parts) >= 3:
                        frontmatter = parts[1]
                        name = ""
                        description = ""
                        for line in frontmatter.split("\n"):
                            if line.strip().startswith("name:"):
                                name = line.split(":", 1)[1].strip()
                            elif line.strip().startswith("description:"):
                                description = line.split(":", 1)[1].strip()
                        
                        self.skills_cache[skill_name] = {
                            "name": name or skill_name,
                            "description": description,
                            "path": skill_path
                        }

    def get_skills_overview(self, agent_id: str):
        from core.loaders.agents_loader import AgentsLoader
        agent = AgentsLoader().get_agent(agent_id)
        allowed_skills = agent.config.get("skills", [])
        
        self._load_skills(allowed_skills)
        overview = "<skills_list>\nThe follow lists the names and descriptions of the skills \
            that you have access to. To use the skill, use the `load_skill` tool with the \
            skill name to load the skill into your memory\n"
        for skill_name, info in self.skills_cache.items():
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
        
        info = self.skills_cache.get(skill_name)
        if not info:
            return f"Skill {skill_name} not found."
            
        skill_path = info.get("path")
        if not skill_path or not os.path.isfile(skill_path):
            return f"Skill file for {skill_name} not found."
            
        with open(skill_path, "r") as f:
            content = f.read()
            
        return f"<skill>\n{content}\n</skill>"
