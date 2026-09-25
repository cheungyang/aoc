import unittest
from unittest.mock import patch
import os
import sys
import datetime
from zoneinfo import ZoneInfo

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.util.config import Config
from core.util.prompt_util import (
    get_knowledge_prompt,
    get_formatting_prompt,
    get_channel_prompt,
    get_agent_memory_prompt,
    get_agent_static_prompt,
)


class TestPromptUtil(unittest.TestCase):

    def tearDown(self):
        Config().timezone = None

    def test_get_knowledge_prompt(self):
        Config().timezone = "America/Los_Angeles"
        prompt = get_knowledge_prompt()
        self.assertIn("<common_knowledge>", prompt)
        self.assertIn("Today's Date:", prompt)
        self.assertNotIn("Current Time:", prompt)  # Omitted to preserve LLM prompt cache prefix

        now = datetime.datetime.now(ZoneInfo("America/Los_Angeles"))
        date_str = now.strftime("%Y-%m-%d")
        self.assertIn(date_str, prompt)
        self.assertIn("America/Los_Angeles", prompt)
        self.assertIn(now.strftime("%Z"), prompt)

        prompt_again = get_knowledge_prompt()
        self.assertEqual(prompt, prompt_again)

    def test_get_knowledge_prompt_uses_configured_timezone(self):
        """The host clock (UTC in prod) must not decide what 'today' is."""
        Config().timezone = "Asia/Tokyo"
        prompt = get_knowledge_prompt()

        tokyo_now = datetime.datetime.now(ZoneInfo("Asia/Tokyo"))
        self.assertIn(tokyo_now.strftime("%Y-%m-%d"), prompt)
        self.assertIn(tokyo_now.strftime("%A"), prompt)
        self.assertIn("Asia/Tokyo", prompt)
        self.assertIn("UTC+09:00", prompt)

    def test_get_knowledge_prompt_falls_back_on_bad_timezone(self):
        Config().timezone = "Not/AZone"
        prompt = get_knowledge_prompt()
        self.assertIn("<common_knowledge>", prompt)
        self.assertIn("Today's Date:", prompt)
        self.assertIn("Current Timezone:", prompt)


    def test_get_formatting_prompt(self):
        prompt = get_formatting_prompt()
        self.assertIn("<formatting_rules>", prompt)
        self.assertIn("<poll allow_multiple=", prompt)
        self.assertIn("<options>", prompt)
        self.assertIn("<images>", prompt)
        self.assertIn("<image path=", prompt)
        self.assertIn("<videos>", prompt)
        self.assertIn("<video path=", prompt)
        self.assertIn("<memory_logging_rules>", prompt)
        self.assertIn("<system_memory_log>", prompt)
        self.assertIn("<tool_execution_rules>", prompt)
        self.assertIn("Permission Restrictions", prompt)
        self.assertIn("Cross-Channel Communication", prompt)

    def test_get_channel_prompt_explicit(self):
        prompt = get_channel_prompt("software-dev")
        self.assertIn("<current_channel_context>", prompt)
        self.assertIn("Discord channel: #software-dev", prompt)

    def test_get_channel_prompt_context_var(self):
        from core.runtime.execution_context import current_execution_context
        from core.runtime.session_manager import SessionManager
        sess = SessionManager.get_session(agent_id="test", source="discord", channel="weekend-planning")
        token = current_execution_context.set(sess)
        try:
            prompt = get_channel_prompt()
            self.assertIn("<current_channel_context>", prompt)
            self.assertIn("Discord channel: #weekend-planning", prompt)
        finally:
            current_execution_context.reset(token)

    def test_get_channel_prompt_thread(self):
        import discord
        from unittest.mock import MagicMock
        from core.runtime.execution_context import current_execution_context
        from core.runtime.session_manager import SessionManager
        mock_thread = MagicMock(spec=discord.Thread)
        mock_thread.id = 999
        mock_thread.name = "sub-topic"
        mock_thread.parent = MagicMock(spec=discord.TextChannel)
        mock_thread.parent.name = "software-dev"
        sess = SessionManager.get_session(agent_id="test", source="discord", channel=mock_thread)
        token = current_execution_context.set(sess)
        try:
            prompt = get_channel_prompt()
            self.assertIn("<current_channel_context>", prompt)
            self.assertIn("Discord thread 'sub-topic' within channel: #software-dev", prompt)
        finally:
            current_execution_context.reset(token)

    def test_get_channel_prompt_empty(self):
        from core.runtime.execution_context import current_execution_context
        token = current_execution_context.set(None)
        try:
            prompt = get_channel_prompt()
            self.assertEqual(prompt, "")
        finally:
            current_execution_context.reset(token)


class TestAgentPrompts(unittest.TestCase):
    """Static files come from agents/<id>/; memory is Memory v2 under the vault."""

    ENTRY = "- [{tag}] {text} (src: test-agent · first 2026-09-01 · seen 2026-09-01 · x1)"

    def setUp(self):
        import tempfile
        from core.util import prompt_util
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.agent_dir = os.path.join(tmp.name, "agent")
        self.pkm = os.path.join(tmp.name, "pkm")
        os.makedirs(self.agent_dir)
        real = prompt_util._agent_prompt_files
        patcher = patch.object(prompt_util, "_agent_prompt_files", side_effect=lambda a: {
            k: (os.path.join(self.agent_dir, os.path.basename(path)), desc)
            for k, (path, desc) in real(a).items()
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        Config().pkm_dir = self.pkm
        self.addCleanup(setattr, Config(), "pkm_dir", None)

    def write(self, rel, text, root=None):
        path = os.path.join(root or self.agent_dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def entry(self, tag, text):
        return self.ENTRY.format(tag=tag, text=text) + "\n"

    def test_agent_static_and_memory_prompts(self):
        for name in ("AGENTS", "INSTRUCTIONS", "IDENTITY", "SOUL"):
            self.write(f"{name}.md", f"# {name}.md\n\n{name.lower()} content")
        self.write("USER.md", "user content")
        self.write("wiki/memory/TAGS.md", "## food\nDiet.\n## travel\nTrips.\n", self.pkm)
        self.write("wiki/memory/PROFILE.md", "# Profile\n\n" + self.entry("profile", "Lives in San Jose."), self.pkm)
        self.write("wiki/memory/topics/food.md", self.entry("food", "Oat milk OK."), self.pkm)
        self.write("wiki/memory/topics/travel.md", self.entry("travel", "Not subscribed."), self.pkm)
        self.write("agents/test-agent/MEMORY.md", self.entry("private", "memory content"), self.pkm)
        self.write("agents/test-agent/FEEDBACK.md", self.entry("feedback", "feedback content"), self.pkm)

        static = get_agent_static_prompt("test-agent")
        memory = get_agent_memory_prompt("test-agent", {"memory_topics": ["food"]})

        self.assertIn("<description>Your purpose, specialization and workflow</description>", static)
        self.assertIn("<content>agents content\n\ninstructions content</content>", static)
        self.assertIn("<content>identity content\n\nsoul content</content>", static)
        self.assertIn("<content>user content\n\n- Lives in San Jose.</content>", memory)
        self.assertIn("<SHARED_MEMORY>", memory)
        self.assertIn("### food\n- Oat milk OK.", memory)
        self.assertNotIn("Not subscribed", memory)
        self.assertIn("<content>- memory content</content>", memory)
        self.assertIn("<content>- feedback content</content>", memory)
        self.assertNotIn("src:", memory)

    def test_stateless_agents_get_no_shared_memory(self):
        self.write("USER.md", "user content")
        self.write("wiki/memory/TAGS.md", "## food\nDiet.\n", self.pkm)
        self.write("wiki/memory/PROFILE.md", self.entry("profile", "Lives in San Jose."), self.pkm)
        self.write("wiki/memory/topics/food.md", self.entry("food", "Oat milk OK."), self.pkm)

        memory = get_agent_memory_prompt("test-agent", {"stateless": True, "memory_topics": ["food"]})

        self.assertIn("<content>user content</content>", memory)
        self.assertNotIn("San Jose", memory)
        self.assertNotIn("SHARED_MEMORY", memory)

    def test_agent_prompts_with_only_instructions(self):
        self.write("INSTRUCTIONS.md", "only instructions content")
        self.write("IDENTITY.md", "identity content")

        prompt = get_agent_static_prompt("test-agent") + get_agent_memory_prompt("test-agent", {})

        self.assertIn("<content>only instructions content</content>", prompt)
        self.assertIn("<content>identity content</content>", prompt)
        self.assertNotIn("<HUMAN_CONTEXT>", prompt)
        self.assertEqual(get_agent_memory_prompt("test-agent", {}), "")

if __name__ == "__main__":
    unittest.main()
