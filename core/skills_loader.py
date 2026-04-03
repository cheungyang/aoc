import os

class SkillsLoader:
    def __init__(self, skills_dir="skills"):
        self.skills_dir = skills_dir

    def load_skills(self):
        skills_text = ""
        if os.path.isdir(self.skills_dir):
            for skill_name in os.listdir(self.skills_dir):
                skill_path = os.path.join(self.skills_dir, skill_name, "SKILL.md")
                if os.path.isfile(skill_path):
                    with open(skill_path, "r") as f:
                        content = f.read()
                    parts = content.split("---")
                    if len(parts) >= 3:
                         # parts[1] is frontmatter, parts[2] is body
                         body = parts[2].strip()
                         skills_text += f"### Skill: {skill_name}\n{body}\n\n"
        return skills_text
