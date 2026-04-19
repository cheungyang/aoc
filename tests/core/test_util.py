import unittest
from unittest.mock import patch, mock_open, MagicMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.util import split_message

class TestUtil(unittest.TestCase):

    def test_split_message_short(self):
        text = "Hello world"
        chunks = split_message(text)
        self.assertEqual(chunks, [text])

    def test_split_message_newline(self):
        text = "Line 1\n" + "a" * 1990 + "\nLine 2"
        chunks = split_message(text)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], "Line 1\n" + "a" * 1990)
        self.assertEqual(chunks[1], "Line 2")

    def test_split_message_hard_split(self):
        text = "a" * 3000
        chunks = split_message(text, limit=1000)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0], "a" * 1000)
        self.assertEqual(chunks[1], "a" * 1000)
        self.assertEqual(chunks[2], "a" * 1000)

    def test_split_message_empty(self):
        chunks = split_message("")
        self.assertEqual(chunks, [])

    def test_split_message_none(self):
        chunks = split_message(None)
        self.assertEqual(chunks, [])

    def test_get_knowledge_prompt(self):
        from core.util import get_knowledge_prompt
        import datetime
        
        prompt = get_knowledge_prompt()
        self.assertIn("<common_knowledge>", prompt)
        self.assertIn("Today's Date:", prompt)
        
        now = datetime.datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        self.assertIn(date_str, prompt)

    def test_get_formatting_prompt(self):
        from core.util import get_formatting_prompt
        prompt = get_formatting_prompt()
        self.assertIn("<formatting_rules>", prompt)
        self.assertIn("<poll allow_multiple=", prompt)
        self.assertIn("<options>", prompt)

    def test_format_tool_response(self):
        from core.util import format_tool_response
        response = format_tool_response("test_tool", "test_payload", "test_errors")
        self.assertIn("<test_tool_response>", response)
        self.assertIn("<payload>test_payload</payload>", response)
        self.assertIn("<errors>test_errors</errors>", response)

    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open)
    def test_get_agent_prompt(self, mock_file, mock_exists):
        mock_exists.return_value = True
        
        file_contents = [
            "agents content",   # AGENTS.md
            "identity content", # IDENTITY.md
            "soul content",     # SOUL.md
            "user content",     # USER.md
            "context content",  # CONTEXT.md
            "memory content",   # MEMORY.md
            "feedback content"  # FEEDBACK.md
        ]
        mocks = [mock_open(read_data=c).return_value for c in file_contents]
        mock_file.side_effect = mocks
        
        from core.util import get_agent_prompt
        prompt = get_agent_prompt("test-agent")
        
        self.assertIn("<SYSTEM_PURPOSE>", prompt)
        self.assertIn("<description>Your purpose, specialization and workflow</description>", prompt)
        self.assertIn("<content>agents content</content>", prompt)
        
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
            "# AGENTS.md\n\nagents content",   # AGENTS.md
            "# IDENTITY.md\nidentity content", # IDENTITY.md
            "# SOUL.md\n\n\nsoul content",     # SOUL.md
            "user content",     # USER.md
            "context content",  # CONTEXT.md
            "memory content",   # MEMORY.md
            "feedback content"  # FEEDBACK.md
        ]
        mocks = [mock_open(read_data=c).return_value for c in file_contents]
        mock_file.side_effect = mocks
        
        from core.util import get_agent_prompt
        prompt = get_agent_prompt("test-agent")
        
        self.assertIn("<SYSTEM_PURPOSE>", prompt)
        self.assertIn("<content>agents content</content>", prompt)
        
        self.assertIn("<PERSONA>", prompt)
        self.assertIn("<content>identity content\n\nsoul content</content>", prompt)
        
        self.assertIn("<HUMAN_CONTEXT>", prompt)
        self.assertIn("<content>user content\n\ncontext content</content>", prompt)
        
        self.assertIn("<MEMORY_AND_PRECEDENTS>", prompt)
        self.assertIn("<content>memory content</content>", prompt)
        
        self.assertIn("<FEEDBACK_TO_ADHERE_TO>", prompt)
        self.assertIn("<content>feedback content</content>", prompt)

if __name__ == "__main__":
    unittest.main()
