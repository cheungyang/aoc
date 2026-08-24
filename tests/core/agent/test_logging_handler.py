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
        handler.last_execution_time = 1.234
        
        handler.on_chain_end({})
        
        handler.manager.append_token_usage.assert_called_once_with(
            "session1", "gemini-pro", 100, 50, 20.0, 1.234
        )

    def test_on_llm_start_and_end_tracks_execution_time(self):
        handler = LoggingHandler(session_id="session1")
        handler.manager = MagicMock()
        
        handler.on_llm_start(None, ["Prompt"])
        self.assertIsNotNone(handler.llm_start_time)
        
        mock_response = MagicMock()
        mock_generation = MagicMock()
        mock_generation.text = "AI Reply"
        mock_message = MagicMock()
        mock_message.usage_metadata = {"input_tokens": 10, "output_tokens": 5}
        mock_generation.message = mock_message
        mock_response.generations = [[mock_generation]]
        mock_response.llm_output = {"model_name": "gemini-pro"}
        
        handler.on_llm_end(mock_response)
        self.assertGreaterEqual(handler.last_execution_time, 0.0)
        self.assertIsNone(handler.llm_start_time)

    def test_on_llm_start_appends_list_human_message_as_json(self):
        msg_list = [{"type": "text", "text": "hello"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}]
        handler = LoggingHandler(session_id="session1", role="user", human_message=msg_list)
        handler.manager = MagicMock()
        
        handler.on_llm_start(None, ["Prompt 1"])
        
        import json
        handler.manager.append_message.assert_called_once_with("session1", "user", json.dumps(msg_list))

    def test_on_llm_start_does_not_duplicate_on_subsequent_calls(self):
        handler = LoggingHandler(session_id="session1", role="user", human_message="hello")
        handler.manager = MagicMock()
        
        handler.on_llm_start(None, ["Prompt 1"])
        handler.on_llm_start(None, ["Prompt 2"])
        
        handler.manager.append_message.assert_called_once_with("session1", "user", "hello")

    def test_on_llm_end_handles_list_content(self):
        handler = LoggingHandler(session_id="session1")
        handler.manager = MagicMock()
        
        mock_response = MagicMock()
        mock_generation = MagicMock()
        mock_generation.text = ""
        mock_message = MagicMock()
        mock_message.content = [{"type": "text", "text": "AI reply list"}]
        mock_message.usage_metadata = None
        mock_generation.message = mock_message
        mock_response.generations = [[mock_generation]]
        mock_response.llm_output = None
        
        handler.on_llm_end(mock_response)
        
        import json
        handler.manager.append_message.assert_called_once_with("session1", "ai", json.dumps([{"type": "text", "text": "AI reply list"}]))

    def test_on_tool_start_and_end_tracks_execution_time(self):
        handler = LoggingHandler(session_id="session1")
        handler.manager = MagicMock()
        
        handler.on_tool_start({"name": "web_search"}, "query_string", run_id="run_123")
        self.assertIn("run_123", handler.tool_start_times)
        
        mock_output = MagicMock()
        mock_output.content = "Search result output"
        
        handler.on_tool_end(mock_output, run_id="run_123")
        self.assertNotIn("run_123", handler.tool_start_times)
        
        self.assertEqual(handler.manager.append_message.call_count, 2)
        start_call = handler.manager.append_message.call_args_list[0][0]
        self.assertEqual(start_call, ("session1", "system", "Tool web_search:query_string"))
        
        end_call = handler.manager.append_message.call_args_list[1][0]
        self.assertEqual(end_call[0], "session1")
        self.assertEqual(end_call[1], "system")
        logged_msg = end_call[2]
        self.assertTrue(logged_msg.startswith("Tool Output ["), f"Message '{logged_msg}' should start with 'Tool Output ['")
        self.assertTrue(logged_msg.endswith("s]: Search result output"), f"Message '{logged_msg}' should end with 's]: Search result output'")

if __name__ == "__main__":
    unittest.main()

