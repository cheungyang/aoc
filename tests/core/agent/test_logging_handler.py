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

if __name__ == "__main__":
    unittest.main()

