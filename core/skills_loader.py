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
        skills_count = 0
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
                         skills_count += 1
        print(f"Cached {skills_count} skills.")

    def get_skill_prompt(self, allowed_skills=None):
        self._load_skills()
        skills_text = ""
        skills_count = 0
        for skill_name, body in self.skills_cache.items():
            if allowed_skills is not None and skill_name not in allowed_skills:
                continue
            skills_text += f"### Skill: {skill_name}\n{body}\n\n"
            skills_count += 1
        print(f"Loaded {skills_count} skills from cache.")
        return skills_text
