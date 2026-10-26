"""Prompt-cache layout: the static system prefix must not move when PKM memory
files or the channel change.

Gemini implicit caching hits only on an identical request prefix, so these
tests pin the block order produced by GraphBuilder._get_prompt_template and
check that rewriting Profile/MEMORY/FEEDBACK (or switching channel) only
changes the tail of the system content.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from langchain_core.messages import HumanMessage, SystemMessage

from core.prompts import system
from core.config.settings import Config
from core.prompts.system import (
    get_agent_memory_prompt,
    get_agent_static_prompt,
    get_tool_rules_prompt,
)
from tests.helpers import make_context


class _AgentFiles:
    """A throwaway agents/<id> checkout plus a vault holding its Memory v2 files."""

    NAMES = {
        "AGENT": "AGENTS.md",
        "INSTRUCTIONS": "INSTRUCTIONS.md",
        "IDENTITY": "IDENTITY.md",
        "SOUL": "SOUL.md",
        "USER": "pkm/agents/test-agent/USER.md",
        "PROFILE": "pkm/wiki/memory/PROFILE.md",
        "MEMORY": "pkm/agents/test-agent/MEMORY.md",
        "FEEDBACK": "pkm/agents/test-agent/FEEDBACK.md",
    }
    ENTRY_FILES = {"PROFILE": "profile", "MEMORY": "private", "FEEDBACK": "feedback"}

    def __init__(self, root):
        self.root = root
        self.write("AGENT", "agents body")
        self.write("INSTRUCTIONS", "instructions body")
        self.write("IDENTITY", "identity body")
        self.write("SOUL", "soul body")
        self.write("USER", "user body")
        self.write("PROFILE", "profile v1")
        self.write("MEMORY", "memory v1")
        self.write("FEEDBACK", "feedback v1")

    def path(self, key):
        return os.path.join(self.root, self.NAMES[key])

    def write(self, key, text):
        if key in self.ENTRY_FILES:
            text = f"- [{self.ENTRY_FILES[key]}] {text} (src: test-agent · first 2026-09-01 · seen 2026-09-01 · x1)\n"
        os.makedirs(os.path.dirname(self.path(key)), exist_ok=True)
        with open(self.path(key), "w") as f:
            f.write(text)

    def files(self, agent_id):
        real = _REAL_AGENT_PROMPT_FILES(agent_id)
        return {k: (self.path(k), desc) for k, (_, desc) in real.items()}

    @property
    def pkm(self):
        return os.path.join(self.root, "pkm")


# Captured before any patching, so descriptions stay authoritative.
_REAL_AGENT_PROMPT_FILES = system._agent_prompt_files


class TestPromptCacheLayout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.agent = _AgentFiles(self.tmp.name)
        Config().pkm_dir = self.agent.pkm
        self.addCleanup(setattr, Config(), "pkm_dir", None)
        patchers = [
            patch.object(system, "_agent_prompt_files", side_effect=self.agent.files),
            patch("core.runtime.graph_builder.SkillsLoader"),
            patch("core.runtime.graph_builder.GraphsLoader"),
            patch("core.runtime.graph_builder.get_knowledge_prompt", return_value="KNOWLEDGE"),
            patch("core.runtime.graph_builder.get_channel_prompt"),
        ]
        mocks = [p.start() for p in patchers]
        for p in patchers:
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

        _, skills_cls, graphs_cls, _, self.mock_channel = mocks
        skills_cls.return_value.get_skills_overview.return_value = "SKILLS"
        graphs_cls.return_value = MagicMock()
        graphs_cls.return_value.get_graphs_overview.return_value = "SUBGRAPHS"
        self.mock_channel.return_value = "CHANNEL general"

    def _system_blocks(self):
        from core.runtime.graph_builder import GraphBuilder

        prompt_fn = GraphBuilder()._get_prompt_template(make_context(agent_id="test-agent"))
        formatted = prompt_fn({"messages": [HumanMessage(content="hi")]})
        self.assertIsInstance(formatted[-1], HumanMessage)
        return [m.content for m in formatted if isinstance(m, SystemMessage)]

    # --- static/memory split ----------------------------------------------

    def test_split_puts_each_block_in_the_right_half(self):
        static = get_agent_static_prompt("test-agent")
        memory = get_agent_memory_prompt("test-agent")

        self.assertIn("<SYSTEM_PURPOSE>", static)
        self.assertIn("<PERSONA>", static)
        for tag in ("HUMAN_CONTEXT", "MEMORY_AND_PRECEDENTS", "FEEDBACK_TO_ADHERE_TO"):
            self.assertNotIn(tag, static)
            self.assertIn(f"<{tag}>", memory)

    def test_static_half_ignores_pkm_files(self):
        before = get_agent_static_prompt("test-agent")
        self.agent.write("MEMORY", "memory v2")
        self.agent.write("PROFILE", "profile v2")
        self.agent.write("FEEDBACK", "feedback v2")
        self.assertEqual(before, get_agent_static_prompt("test-agent"))

    # --- GraphBuilder order -----------------------------------------------

    def test_block_order_static_then_memory_then_channel(self):
        blocks = self._system_blocks()
        self.assertEqual(len(blocks), 8)
        formatting, agent_static, tool_rules, skills, subgraphs, knowledge, memory, channel = blocks

        self.assertIn("<formatting_rules>", formatting)
        self.assertEqual(agent_static, get_agent_static_prompt("test-agent"))
        self.assertEqual(tool_rules, get_tool_rules_prompt("test-agent"))
        self.assertIn("<TOOL_RULES>", tool_rules)
        self.assertEqual((skills, subgraphs, knowledge), ("SKILLS", "SUBGRAPHS", "KNOWLEDGE"))
        self.assertEqual(memory, get_agent_memory_prompt("test-agent"))
        self.assertEqual(channel, "CHANNEL general")

    def test_memory_write_leaves_static_prefix_unchanged(self):
        before = self._system_blocks()
        self.agent.write("MEMORY", "memory v2 -- a new precedent")
        self.agent.write("FEEDBACK", "feedback v2")
        self.agent.write("PROFILE", "profile v2")
        after = self._system_blocks()

        # Everything up to and including the daily knowledge block is identical.
        self.assertEqual(before[:6], after[:6])
        self.assertNotEqual(before[6], after[6])
        self.assertIn("memory v2", after[6])
        self.assertEqual(before[7], after[7])

    def test_channel_change_leaves_memory_and_static_prefix_unchanged(self):
        before = self._system_blocks()
        self.mock_channel.return_value = "CHANNEL other"
        after = self._system_blocks()
        self.assertEqual(before[:7], after[:7])
        self.assertEqual(after[7], "CHANNEL other")

    def test_missing_pkm_files_add_no_blank_system_block(self):
        for key in ("USER", "PROFILE", "MEMORY", "FEEDBACK"):
            os.remove(self.agent.path(key))
        self.mock_channel.return_value = ""
        blocks = self._system_blocks()
        self.assertEqual(len(blocks), 6)
        self.assertTrue(all(b.strip() for b in blocks))

    def test_voice_mode_comes_last_and_leaves_the_prefix_unchanged(self):
        """A voice turn shares every cached block with a typed turn in the same channel."""
        typed = self._system_blocks()
        with patch("core.runtime.graph_builder.get_voice_mode_prompt", return_value="<voice_mode>V</voice_mode>"):
            spoken = self._system_blocks()
        self.assertEqual(spoken[:-1], typed)
        self.assertEqual(spoken[-1], "<voice_mode>V</voice_mode>")


if __name__ == "__main__":
    unittest.main()
