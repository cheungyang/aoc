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

    def _load_skills(self):
        if self.skills_cache:
            return
        if os.path.isdir(self.skills_dir):
            for skill_name in os.listdir(self.skills_dir):
                skill_path = os.path.join(self.skills_dir, skill_name, "SKILL.md")
                if os.path.isfile(skill_path):
                    with open(skill_path, "r") as f:
                        content = f.read()
                    parts = content.split("---")
                    if len(parts) >= 3:
                         body = parts[2].strip()
                         self.skills_cache[skill_name] = body

    def get_skill_prompt(self, allowed_skills=None):
        self._load_skills()
        skills_text = ""
        skill_names = []
        for skill_name, body in self.skills_cache.items():
            if allowed_skills is not None and skill_name not in allowed_skills:
                continue
            skills_text += f"### Skill: {skill_name}\n{body}\n\n"
            skill_names.append(skill_name)
        print(f"Loaded {len(skill_names)} skills, {skill_names}")
        return skills_text
