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
            for skill_id in os.listdir(self.skills_dir):
                if allowed_skills is not None and skill_id not in allowed_skills:
                    continue
                if skill_id in self._skills_cache:
                    continue
                
                skill_path = os.path.join(self.skills_dir, skill_id, "skill.json")
                if os.path.isfile(skill_path):
                    try:
                        with open(skill_path, "r") as f:
                            config = json.load(f)
                        config["path"] = os.path.join(self.skills_dir, skill_id, "SKILL.md")
                        self._skills_cache[skill_id] = config
                        HotReloader().watch(skill_path, self._on_skill_changed)
                    except Exception as e:
                        print(f"Error loading skill.json for {skill_id}: {e}")

    def get_skill_tools(self, skill_id: str):
        self._load_skills()
        info = self._skills_cache.get(skill_id)
        if not info:
            return {}
        return info.get("tools", {})

    @staticmethod
    def _require_ctx(ctx, caller: str):
        from core.agent.execution_context import ExecutionContext
        if not isinstance(ctx, ExecutionContext):
            raise TypeError(
                f"SkillsLoader.{caller}() expects an ExecutionContext, got {type(ctx).__name__}."
            )
        return ctx

    def resolve_allowed_skills(self, agent_id: str, graph_id: str = None):
        """
        Resolves the skill list for an already-resolved (agent_id, graph_id) pair.

        Internal entry point for callers that have already decided which graph is active
        (e.g. ToolsLoader). Public callers should use get_allowed_skills(ctx).
        """
        from core.loaders.agents_loader import AgentsLoader
        from core.loaders.graphs_loader import GraphsLoader

        agent = AgentsLoader().get_agent(agent_id)
        allowed_skills = agent.config.get("skills", []).copy()

        active_graph = graph_id or agent.config.get("graph", "main")
        if active_graph:
            graph_skills = GraphsLoader().get_graph_skills(active_graph)
            for skill in graph_skills:
                if skill not in allowed_skills:
                    allowed_skills.append(skill)

        return allowed_skills

    def get_allowed_skills(self, ctx):
        self._require_ctx(ctx, "get_allowed_skills")
        return self.resolve_allowed_skills(ctx.agent_id, ctx.graph_id)

    def get_skills_overview(self, ctx):
        self._require_ctx(ctx, "get_skills_overview")
        allowed_skills = self.get_allowed_skills(ctx)

        self._load_skills(allowed_skills)
        overview = "<skills_list>\nThe following lists the names and descriptions of the skills \n\
            that you have access to. To use a skill, use the `load_skill` tool with the \n\
            skill name to load the skill into your memory.\n"

        for skill_id in sorted(allowed_skills):
            info = self._skills_cache.get(skill_id)
            if not info:
                continue
            name = info.get("name", skill_id)
            skill_id_val = info.get("skill_id", skill_id)
            desc = info.get("description", "")
            overview += f"- {name} (id:{skill_id_val}): {desc}\n"
        overview += "</skills_list>"
        return overview

    def get_skill_prompt(self, ctx, skill_id: str):
        self._require_ctx(ctx, "get_skill_prompt")
        allowed_skills = self.get_allowed_skills(ctx)

        if skill_id not in allowed_skills:
            return f"Error: Agent {ctx.agent_id} does not have access to skill {skill_id}."
            
        self._load_skills(allowed_skills)
        
        info = self._skills_cache.get(skill_id)
        if not info:
            return f"Skill {skill_id} not found."
            
        skill_path = info.get("path")
        if not skill_path or not os.path.isfile(skill_path):
            return f"Skill file for {skill_id} not found."
            
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

