"""Checks on the checked-in agent configs and the skills they preload.

These run against the real agents/ and skills/ directories, so a config or
skill edit that would be rejected at load time -- or would bloat every prompt
an agent sends -- fails CI instead of production.
"""
import json
import os
import re
import unittest
from unittest.mock import MagicMock, patch

from core.loaders.agent_schema import known_skill_ids, validate_agent_config
from core.loaders.skills_loader import PRELOAD_TOKEN_BUDGET, core_section, skill_body

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
AGENTS_DIR = os.path.join(ROOT, "agents")
SKILLS_DIR = os.path.join(ROOT, "skills")


def agent_configs():
    for name in sorted(os.listdir(AGENTS_DIR)):
        path = os.path.join(AGENTS_DIR, name, "agent.json")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                yield name, json.load(f)


def preloaded_text(skill_id, mode):
    with open(os.path.join(SKILLS_DIR, skill_id, "SKILL.md"), encoding="utf-8") as f:
        text = f.read()
    return skill_body(text) if mode == "full" else core_section(text)


class TestCheckedInConfigs(unittest.TestCase):

    def test_every_agent_config_validates(self):
        known = known_skill_ids(SKILLS_DIR)
        for name, config in agent_configs():
            with self.subTest(agent=name):
                self.assertEqual(validate_agent_config(config, known), [])

    def test_every_voice_is_a_real_chirp3_hd_voice(self):
        from core.voice.settings import DEFAULT_VOICE, chirp3_voice

        self.assertIsNotNone(chirp3_voice(DEFAULT_VOICE))
        for name, config in agent_configs():
            if "voice" in config:
                with self.subTest(agent=name):
                    self.assertIsNotNone(chirp3_voice(config["voice"]["name"]), config["voice"]["name"])

    def test_core_preloads_have_a_core_section(self):
        for name, config in agent_configs():
            for skill_id, settings in (config.get("skills") or {}).items():
                if settings.get("preload") == "core":
                    with self.subTest(agent=name, skill=skill_id):
                        self.assertTrue(preloaded_text(skill_id, "core"), f"{skill_id} has no '## Core' section")

    def test_preloaded_text_fits_the_budget(self):
        """Preloaded skills are in every prompt the agent sends; keep them small."""
        for name, config in agent_configs():
            modes = {s: v["preload"] for s, v in (config.get("skills") or {}).items() if v.get("preload")}
            tokens = sum(len(preloaded_text(s, m) or "") // 4 for s, m in modes.items())
            with self.subTest(agent=name):
                self.assertLessEqual(tokens, PRELOAD_TOKEN_BUDGET, f"{name} preloads ~{tokens} tokens: {modes}")

    def test_no_instruction_tells_an_agent_to_load_a_skill_it_preloads_in_full(self):
        """A fully preloaded skill is already in the prompt, and the agent may have no load_skill tool."""
        for name, config in agent_configs():
            full = [s for s, v in (config.get("skills") or {}).items() if v.get("preload") == "full"]
            texts = [json.dumps(config.get("schedules", []))]
            for md in ("AGENTS.md", "INSTRUCTIONS.md", "SOUL.md"):
                path = os.path.join(AGENTS_DIR, name, md)
                if os.path.isfile(path):
                    with open(path, encoding="utf-8") as f:
                        texts.append(f.read())
            for skill_id in full:
                pattern = re.compile(rf"load(?:_skill)?\W+(?:\w+\W+){{0,3}}`?{skill_id}`?", re.IGNORECASE)
                with self.subTest(agent=name, skill=skill_id):
                    self.assertFalse(any(pattern.search(t) for t in texts))


class TestAgentCreationTemplate(unittest.TestCase):
    """agent-designer writes agent.json from this template; a key it would drop or
    invent should fail here rather than when the new agent is loaded."""

    def template(self):
        with open(os.path.join(SKILLS_DIR, "agent_creation", "SKILL.md"), encoding="utf-8") as f:
            text = f.read()
        match = re.search(r"```json\n(\{.*?\})\n```", text, re.DOTALL)
        self.assertIsNotNone(match, "agent_creation has no JSON template")
        return json.loads(match.group(1))

    def test_template_passes_validation(self):
        self.assertEqual(validate_agent_config(self.template(), known_skill_ids(SKILLS_DIR)), [])

    def test_template_shows_every_new_parameter(self):
        template = self.template()
        self.assertIsInstance(template["skills"], dict)
        self.assertTrue(any(v.get("preload") for v in template["skills"].values()))
        self.assertIn("thinking", template)
        self.assertIn("voice", template)


class TestRealAgentsLoad(unittest.TestCase):

    def test_loader_accepts_every_checked_in_agent(self):
        from core.loaders.agents_loader import AgentsLoader

        loader = AgentsLoader()
        loader._load_agents()
        self.assertEqual(sorted(loader.list_agent_ids()), sorted(name for name, _ in agent_configs()))


if __name__ == "__main__":
    unittest.main()
