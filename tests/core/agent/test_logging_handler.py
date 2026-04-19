import unittest
import os
import sys
from unittest.mock import MagicMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.agent.logging_handler import LoggingHandler

class TestLoggingHandler(unittest.TestCase):

    def test_on_llm_start_appends_to_session(self):
        handler = LoggingHandler(session_id="session1", role="user", human_message="hello")
        handler.manager = MagicMock()
        
        handler.on_llm_start(None, ["Prompt 1"])
        
        handler.manager.append_message.assert_called_once_with("session1", "user", "hello")

    def test_on_llm_end_appends_to_session(self):
        handler = LoggingHandler(session_id="session1")
        handler.manager = MagicMock()
        
        mock_response = MagicMock()
        mock_generation = MagicMock()
        mock_generation.text = "AI Reply"
        mock_response.generations = [[mock_generation]]
        
        handler.on_llm_end(mock_response)
        
        handler.manager.append_message.assert_called_once_with("session1", "ai", "AI Reply")

    def test_on_tool_start_appends_to_session(self):
        handler = LoggingHandler(session_id="session1")
        handler.manager = MagicMock()
        
        handler.on_tool_start({"name": "MyTool"}, "input_args")
        
        handler.manager.append_message.assert_called_once_with("session1", "system", "Tool MyTool:input_args")

    def test_on_tool_start_extracts_extra_info(self):
        handler = LoggingHandler(session_id="session1")
        handler.manager = MagicMock()
        
        input_str = "{'action': 'create', 'path': '/tmp', 'skill_id': 'skill_123'}"
        handler.on_tool_start({"name": "MyTool"}, input_str)
        
        handler.manager.append_message.assert_called_once_with(
            "session1", 
            "system", 
            "Tool MyTool [action: create, path: /tmp, skill_id: skill_123]:{'action': 'create', 'path': '/tmp', 'skill_id': 'skill_123'}"
        )
    def test_on_tool_end_appends_to_session(self):
        handler = LoggingHandler(session_id="session1")
        handler.manager = MagicMock()
        
        mock_output = MagicMock()
        mock_output.content = "Tool result"
        
        handler.on_tool_end(mock_output)
        
        handler.manager.append_message.assert_called_once_with("session1", "system", "Tool Output: Tool result")

    def test_on_tool_end_string_output_appends_to_session(self):
        handler = LoggingHandler(session_id="session1")
        handler.manager = MagicMock()
        
        handler.on_tool_end("Simple string output")
        
        handler.manager.append_message.assert_called_once_with("session1", "system", "Tool Output: Simple string output")

    def test_on_llm_end_extracts_token_usage(self):
        handler = LoggingHandler(session_id="session1")
        handler.manager = MagicMock()
        
        mock_response = MagicMock()
        mock_generation = MagicMock()
        mock_generation.text = "AI Reply"
        mock_message = MagicMock()
        mock_message.usage_metadata = {"input_tokens": 10, "output_tokens": 5}
        mock_generation.message = mock_message
        mock_response.generations = [[mock_generation]]
        mock_response.llm_output = {"model_name": "gemini-pro"}
        
        handler.on_llm_end(mock_response)
        
        self.assertEqual(handler.last_token_usage["input_tokens"], 10)
        self.assertEqual(handler.last_token_usage["output_tokens"], 5)
        self.assertEqual(handler.last_token_usage["model"], "gemini-pro")

    def test_on_chain_end_logs_token_usage(self):
        handler = LoggingHandler(session_id="session1")
        handler.manager = MagicMock()
        handler.last_token_usage = {
            "input_tokens": 100,
            "output_tokens": 50,
            "model": "gemini-pro",
            "input_token_details": {"cache_read": 20}
        }
        
        handler.on_chain_end({})
        
        handler.manager.append_token_usage.assert_called_once_with(
            "session1", "gemini-pro", 100, 50, 20.0
        )

if __name__ == "__main__":
    unittest.main()

