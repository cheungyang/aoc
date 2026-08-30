import unittest
from unittest.mock import patch, mock_open
import os
import sys
import datetime

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.util.prompt_util import (
    get_knowledge_prompt,
    get_formatting_prompt,
    get_channel_prompt,
    get_agent_prompt,
)


class TestPromptUtil(unittest.TestCase):

    def test_get_knowledge_prompt(self):
        prompt = get_knowledge_prompt()
        self.assertIn("<common_knowledge>", prompt)
        self.assertIn("Today's Date:", prompt)
        self.assertNotIn("Current Time:", prompt)  # Omitted to preserve LLM prompt cache prefix
        
        now = datetime.datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        self.assertIn(date_str, prompt)

        prompt_again = get_knowledge_prompt()
        self.assertEqual(prompt, prompt_again)

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
        from core.agent.job_manager import current_session_identifier
        from core.agent.session_manager import SessionManager
        sess = SessionManager.get_session(agent_id="test", source="discord", channel="weekend-planning")
        token = current_session_identifier.set(sess)
        try:
            prompt = get_channel_prompt()
            self.assertIn("<current_channel_context>", prompt)
            self.assertIn("Discord channel: #weekend-planning", prompt)
        finally:
            current_session_identifier.reset(token)

    def test_get_channel_prompt_thread(self):
        import discord
        from unittest.mock import MagicMock
        from core.agent.job_manager import current_session_identifier
        from core.agent.session_manager import SessionManager
        mock_thread = MagicMock(spec=discord.Thread)
        mock_thread.id = 999
        mock_thread.name = "sub-topic"
        mock_thread.parent = MagicMock(spec=discord.TextChannel)
        mock_thread.parent.name = "software-dev"
        sess = SessionManager.get_session(agent_id="test", source="discord", channel=mock_thread)
        token = current_session_identifier.set(sess)
        try:
            prompt = get_channel_prompt()
            self.assertIn("<current_channel_context>", prompt)
            self.assertIn("Discord thread 'sub-topic' within channel: #software-dev", prompt)
        finally:
            current_session_identifier.reset(token)

    def test_get_channel_prompt_empty(self):
        from core.agent.job_manager import current_session_identifier
        token = current_session_identifier.set(None)
        try:
            prompt = get_channel_prompt()
            self.assertEqual(prompt, "")
        finally:
            current_session_identifier.reset(token)

    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open)
    def test_get_agent_prompt(self, mock_file, mock_exists):
        mock_exists.return_value = True
        
        file_contents = [
            "agents content",        # AGENTS.md
            "instructions content",  # INSTRUCTIONS.md
            "identity content",      # IDENTITY.md
            "soul content",          # SOUL.md
            "user content",          # USER.md
            "context content",       # CONTEXT.md
            "memory content",        # MEMORY.md
            "feedback content"       # FEEDBACK.md
        ]
        mocks = [mock_open(read_data=c).return_value for c in file_contents]
        mock_file.side_effect = mocks
        
        prompt = get_agent_prompt("test-agent")
        
        self.assertIn("<SYSTEM_PURPOSE>", prompt)
        self.assertIn("<description>Your purpose, specialization and workflow</description>", prompt)
        self.assertIn("<content>agents content\n\ninstructions content</content>", prompt)
        
        self.assertIn("<PERSONA>", prompt)
        self.assertIn("<description>This is who you are and how you behave</description>", prompt)
        self.assertIn("<content>identity content\n\nsoul content</content>", prompt)
        
        self.assertIn("<HUMAN_CONTEXT>", prompt)
        self.assertIn("<content>user content\n\ncontext content</content>", prompt)
        
        self.assertIn("<MEMORY_AND_PRECEDENTS>", prompt)
        self.assertIn("<content>memory content</content>", prompt)
        
        self.assertIn("<FEEDBACK_TO_ADHERE_TO>", prompt)
        self.assertIn("<content>feedback content</content>", prompt)

    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open)
    def test_get_agent_prompt_with_headers(self, mock_file, mock_exists):
        mock_exists.return_value = True
        
        file_contents = [
            "# AGENTS.md\n\nagents content",             # AGENTS.md
            "# INSTRUCTIONS.md\n\ninstructions content", # INSTRUCTIONS.md
            "# IDENTITY.md\nidentity content",           # IDENTITY.md
            "# SOUL.md\n\n\nsoul content",               # SOUL.md
            "user content",                              # USER.md
            "context content",                           # CONTEXT.md
            "memory content",                            # MEMORY.md
            "feedback content"                           # FEEDBACK.md
        ]
        mocks = [mock_open(read_data=c).return_value for c in file_contents]
        mock_file.side_effect = mocks
        
        prompt = get_agent_prompt("test-agent")
        
        self.assertIn("<SYSTEM_PURPOSE>", prompt)
        self.assertIn("<content>agents content\n\ninstructions content</content>", prompt)
        
        self.assertIn("<PERSONA>", prompt)
        self.assertIn("<content>identity content\n\nsoul content</content>", prompt)
        
        self.assertIn("<HUMAN_CONTEXT>", prompt)
        self.assertIn("<content>user content\n\ncontext content</content>", prompt)
        
        self.assertIn("<MEMORY_AND_PRECEDENTS>", prompt)
        self.assertIn("<content>memory content</content>", prompt)
        
        self.assertIn("<FEEDBACK_TO_ADHERE_TO>", prompt)
        self.assertIn("<content>feedback content</content>", prompt)

    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open)
    def test_get_agent_prompt_with_only_instructions(self, mock_file, mock_exists):
        def fake_exists(path):
            return path.endswith("INSTRUCTIONS.md") or path.endswith("IDENTITY.md")
        mock_exists.side_effect = fake_exists
        
        file_contents = [
            "only instructions content", # INSTRUCTIONS.md
            "identity content",          # IDENTITY.md
        ]
        mocks = [mock_open(read_data=c).return_value for c in file_contents]
        mock_file.side_effect = mocks
        
        prompt = get_agent_prompt("test-agent")
        
        self.assertIn("<SYSTEM_PURPOSE>", prompt)
        self.assertIn("<content>only instructions content</content>", prompt)
        self.assertIn("<PERSONA>", prompt)
        self.assertIn("<content>identity content</content>", prompt)
        self.assertNotIn("<HUMAN_CONTEXT>", prompt)


if __name__ == "__main__":
    unittest.main()
