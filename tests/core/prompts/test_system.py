"""The system prompt's blocks (core/prompts/system.py)."""
import unittest
from unittest.mock import patch
import os
import sys
import datetime
from zoneinfo import ZoneInfo

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.config.settings import Config
from core.prompts.system import (
    get_knowledge_prompt,
    get_formatting_prompt,
    get_channel_prompt,
    get_agent_memory_prompt,
    get_agent_static_prompt,
    get_tool_rules_prompt,
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

    def test_knowledge_prompt_has_the_iso_week_and_is_stable_within_the_day(self):
        tz = ZoneInfo("America/Los_Angeles")
        morning = datetime.datetime(2026, 10, 16, 6, 42, tzinfo=tz)
        evening = datetime.datetime(2026, 10, 16, 21, 5, tzinfo=tz)
        with patch("core.prompts.system.get_local_now", return_value=morning):
            first = get_knowledge_prompt()
        with patch("core.prompts.system.get_local_now", return_value=evening):
            second = get_knowledge_prompt()
        self.assertIn("Friday (Weekday), ISO week 2026-W42", first)
        self.assertEqual(first, second, "the time of day must not reach the cached system prompt")

    def test_user_messages_carry_the_local_time(self):
        from core.prompts.system import stamp_user_message

        now = datetime.datetime(2026, 10, 16, 6, 42, tzinfo=ZoneInfo("America/Los_Angeles"))
        with patch("core.prompts.system.get_local_now", return_value=now):
            self.assertEqual(stamp_user_message("lights off"), "[Fri 06:42] lights off")
            parts = [{"type": "image_url", "image_url": "x"}]
            self.assertEqual(stamp_user_message(parts), [{"type": "text", "text": "[Fri 06:42]"}, *parts])

    def test_the_stamp_pattern_reads_every_stamp_written(self):
        """What the hand-off rule strips to find a name at the start of your
        words (core/runtime/foundation/addressing.py)."""
        from core.prompts.system import TIME_STAMP, time_stamp

        tz = ZoneInfo("America/Los_Angeles")
        for day in range(19, 26):  # a Monday through a Sunday
            for hour, minute in ((0, 0), (9, 5), (23, 59)):
                stamp = time_stamp(datetime.datetime(2026, 10, day, hour, minute, tzinfo=tz))
                with self.subTest(stamp=stamp):
                    self.assertTrue(TIME_STAMP.fullmatch(stamp))

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
        from core.runtime.foundation.context import current_execution_context
        from core.runtime.foundation.sessions import SessionManager
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
        from core.runtime.foundation.context import current_execution_context
        from core.runtime.foundation.sessions import SessionManager
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
        from core.runtime.foundation.context import current_execution_context
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
        from core.prompts import system
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.agent_dir = os.path.join(tmp.name, "agent")
        self.pkm = os.path.join(tmp.name, "pkm")
        os.makedirs(self.agent_dir)
        real = system._agent_prompt_files
        patcher = patch.object(system, "_agent_prompt_files", side_effect=lambda a: {
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
        self.write("agents/test-agent/USER.md", "user content", self.pkm)
        self.write("wiki/memory/TAGS.md", "## food\nDiet.\n## travel\nTrips.\n", self.pkm)
        self.write("wiki/memory/PROFILE.md", "# Profile\n\n" + self.entry("profile", "Lives in Springfield."), self.pkm)
        self.write("wiki/memory/topics/food.md", self.entry("food", "Oat milk OK."), self.pkm)
        self.write("wiki/memory/topics/travel.md", self.entry("travel", "Not subscribed."), self.pkm)
        self.write("agents/test-agent/MEMORY.md", self.entry("private", "memory content"), self.pkm)
        self.write("agents/test-agent/FEEDBACK.md", self.entry("feedback", "feedback content"), self.pkm)

        static = get_agent_static_prompt("test-agent")
        memory = get_agent_memory_prompt("test-agent", {"memory_topics": ["food"]})

        self.assertIn("<description>Your purpose, specialization and workflow</description>", static)
        self.assertIn("<content>agents content\n\ninstructions content</content>", static)
        self.assertIn("<content>identity content\n\nsoul content</content>", static)
        self.assertIn("<content>user content\n\n- Lives in Springfield.</content>", memory)
        self.assertIn("<SHARED_MEMORY>", memory)
        self.assertIn("### food\n- Oat milk OK.", memory)
        self.assertNotIn("Not subscribed", memory)
        self.assertIn("<content>- memory content</content>", memory)
        self.assertIn("<content>- feedback content</content>", memory)
        self.assertNotIn("src:", memory)

    def test_stateless_agents_get_no_shared_memory(self):
        self.write("agents/test-agent/USER.md", "user content", self.pkm)
        self.write("wiki/memory/TAGS.md", "## food\nDiet.\n", self.pkm)
        self.write("wiki/memory/PROFILE.md", self.entry("profile", "Lives in Springfield."), self.pkm)
        self.write("wiki/memory/topics/food.md", self.entry("food", "Oat milk OK."), self.pkm)

        memory = get_agent_memory_prompt("test-agent", {"stateless": True, "memory_topics": ["food"]})

        self.assertIn("<content>user content</content>", memory)
        self.assertNotIn("Springfield", memory)
        self.assertNotIn("SHARED_MEMORY", memory)

    def test_agent_prompts_with_only_instructions(self):
        self.write("INSTRUCTIONS.md", "only instructions content")
        self.write("IDENTITY.md", "identity content")

        prompt = get_agent_static_prompt("test-agent") + get_agent_memory_prompt("test-agent", {})

        self.assertIn("<content>only instructions content</content>", prompt)
        self.assertIn("<content>identity content</content>", prompt)
        self.assertNotIn("<HUMAN_CONTEXT>", prompt)
        self.assertEqual(get_agent_memory_prompt("test-agent", {}), "")

    def test_every_agent_gets_the_tool_rules(self):
        prompt = get_tool_rules_prompt("test-agent")
        self.assertTrue(prompt.startswith("<TOOL_RULES>"))
        self.assertIn(
            "When a tool says partial or failed, tell the user in one line what's missing. "
            "Don't fill the gap with a guess. Offer the fix.", prompt)
        self.assertIn("`[MEMORY] Tool gap: <tool>, <file>: <what was missing or asked for>.`", prompt)
        self.assertNotIn("WORKFLOW.md", prompt)
        self.assertNotIn("RULES.md", prompt)

    def test_an_agent_with_a_workflow_and_rules_is_pointed_to_them(self):
        self.write("WORKFLOW.md", "# test-agent\n")
        self.write("lib/rules.py", "RULES = ()\n")
        self.write("agents/test-agent/RULES.md", "- move_pct: 3\n", self.pkm)
        prompt = get_tool_rules_prompt("test-agent")
        self.assertIn("agents/test-agent/WORKFLOW.md", prompt)
        self.assertIn("pkm/agents/test-agent/RULES.md", prompt)
        self.assertNotIn("move_pct", prompt)  # the PKM is never read: the block stays static

    def test_an_agent_on_gemini_is_offered_the_next_thinking_marker(self):
        from core.runtime.model.thinking import MARKER_CLOSE, MARKER_OPEN

        prompt = get_tool_rules_prompt("test-agent")  # no agent.json: Gemini, the default
        self.assertIn(f"`{MARKER_OPEN}LEVEL{MARKER_CLOSE}`", prompt, "the tags the model wrapper takes out")
        self.assertIn("minimal, low, medium or high", prompt)
        self.assertIn("covers your next model call only (voice turns cap it at medium)", prompt)
        self.write("agent.json", '{"model": "PRO", "thinking": {"text": "low"}}')
        self.assertIn(MARKER_OPEN, get_tool_rules_prompt("test-agent"))

    def test_stateless_workers_and_other_providers_are_not(self):
        for config in ('{"stateless": true}', '{"provider": "local"}', '{"provider": "ollama"}'):
            with self.subTest(config=config):
                self.write("agent.json", config)
                self.assertNotIn("next_thinking", get_tool_rules_prompt("test-agent"))

if __name__ == "__main__":
    unittest.main()
