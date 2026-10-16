import os
from typing import Dict, List, Optional

from core.loaders.hot_reloader import HotReloader

# An agent's preloaded skill text is part of every one of its prompts, so it
# is capped. ~4 characters per token, the same estimate the context pruner uses.
PRELOAD_TOKEN_BUDGET = 1500


def skill_body(text: str) -> str:
    """A SKILL.md without its YAML frontmatter."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            newline = text.find("\n", end + 4)
            return text[newline + 1:].strip() if newline != -1 else ""
    return text.strip()


def core_section(text: str) -> Optional[str]:
    """The text between a SKILL.md's `## Core` and `## Reference` lines.

    None when the file has no `## Core` line. Without a `## Reference` line,
    Core runs to the end of the file.
    """
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == "## Core"), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip() == "## Reference"), len(lines))
    return "\n".join(lines[start + 1:end]).strip()


class SkillsLoader:
    _instance = None

    def __new__(cls, skills_dir="skills"):
        if cls._instance is None:
            cls._instance = super(SkillsLoader, cls).__new__(cls)
            cls._instance.skills_dir = skills_dir
            cls._instance._skills_cache = {}
            cls._instance._text_cache = {}
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
        from core.runtime.execution_context import ExecutionContext
        if not isinstance(ctx, ExecutionContext):
            raise TypeError(
                f"SkillsLoader.{caller}() expects an ExecutionContext, got {type(ctx).__name__}."
            )
        return ctx

    @staticmethod
    def _agent_skills(agent_id: str) -> Dict[str, dict]:
        """The agent's `skills` object: skill id -> its settings."""
        from core.loaders.agents_loader import AgentsLoader
        return AgentsLoader().get_agent(agent_id).config.get("skills") or {}

    def resolve_allowed_skills(self, agent_id: str, graph_id: str = None):
        """
        Resolves the skill list for an already-resolved (agent_id, graph_id) pair.

        Internal entry point for callers that have already decided which graph is active
        (e.g. ToolsLoader). Public callers should use get_allowed_skills(ctx).
        """
        from core.loaders.agents_loader import AgentsLoader
        from core.loaders.graphs_loader import GraphsLoader

        agent = AgentsLoader().get_agent(agent_id)
        allowed_skills = list(agent.config.get("skills") or {})

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

    def preload_modes(self, ctx) -> Dict[str, str]:
        """Skill id -> "full" or "core", for the agent's preloaded skills.

        Only the agent's own `skills` object preloads; skills a graph grants
        are always on demand.
        """
        self._require_ctx(ctx, "preload_modes")
        return {
            skill_id: settings["preload"]
            for skill_id, settings in self._agent_skills(ctx.agent_id).items()
            if isinstance(settings, dict) and settings.get("preload")
        }

    def needs_load_skill(self, ctx) -> bool:
        """Whether the agent has anything left to fetch with `load_skill`.

        False when every skill it can use is preloaded in full, so the tool's
        schema isn't sent with every request for nothing. A core preload still
        needs it: its Reference section is read on demand.
        """
        modes = self.preload_modes(ctx)
        return any(modes.get(skill_id) != "full" for skill_id in self.get_allowed_skills(ctx))

    def _read_skill_file(self, path: str) -> Optional[str]:
        """A SKILL.md's text, re-read only when the file changes."""
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None
        cached = self._text_cache.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        with open(path, "r") as f:
            text = f.read()
        self._text_cache[path] = (mtime, text)
        return text

    def preloaded_text(self, skill_id: str, mode: str) -> Optional[str]:
        """What a preloaded skill contributes to the prompt: its body, or its Core section.

        None when the skill or its file can't be found, or a core preload has no
        `## Core` section; the caller decides how to say so.
        """
        self._load_skills([skill_id])
        info = self._skills_cache.get(skill_id)
        path = info.get("path") if info else None
        text = self._read_skill_file(path) if path else None
        if text is None:
            return None
        return skill_body(text) if mode == "full" else core_section(text)

    def get_skills_overview(self, ctx):
        self._require_ctx(ctx, "get_skills_overview")
        allowed_skills = self.get_allowed_skills(ctx)
        if not allowed_skills:
            return ""

        self._load_skills(allowed_skills)
        modes = self.preload_modes(ctx)
        overview = (
            "<skills_list>\n"
            "Your skills. Those marked [preloaded] are already in this prompt, below: follow them\n"
            "directly. To read any other skill, or the Reference section of a skill preloaded as\n"
            "core, call the `load_skill` tool with the skill id.\n"
        )
        preloaded = []
        for skill_id in sorted(allowed_skills):
            info = self._skills_cache.get(skill_id)
            if not info:
                continue
            name = info.get("name", skill_id)
            skill_id_val = info.get("skill_id", skill_id)
            desc = info.get("description", "")
            mode = modes.get(skill_id)
            overview += f"- {name} (id:{skill_id_val}): {desc}{' [preloaded]' if mode else ''}\n"
            if mode:
                preloaded.append((skill_id, mode))
        overview += "</skills_list>"

        for skill_id, mode in preloaded:
            text = self.preloaded_text(skill_id, mode)
            if text is None:
                print(f"[SkillsLoader] {skill_id} is preloaded as {mode} but its text is missing; "
                      "the agent will have to load it.")
                text = f'(Not available here; call load_skill("{skill_id}") before using this skill.)'
            overview += f'\n<skill id="{skill_id}" preload="{mode}">\n{text}\n</skill>'
        return overview

    def preloaded_tokens(self, ctx) -> int:
        """Estimated tokens of preloaded skill text in this agent's prompt (~4 chars/token)."""
        return sum(
            len(self.preloaded_text(skill_id, mode) or "") // 4
            for skill_id, mode in self.preload_modes(ctx).items()
        )

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
        self._text_cache.clear()

    def _on_skill_changed(self, file_path):
        print(f"SkillsLoader: skill configuration updated at {file_path}, voiding cache.")
        self.clear_skills_cache()
        from core.loaders.tools_loader import ToolsLoader
        ToolsLoader().clear_permissions_cache()

