import unittest
import os
import sys
from unittest.mock import MagicMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.agent.logging_handler import LoggingHandler, format_tool_extra_str
from core.agent.session_manager import SessionManager


class TestLoggingHandler(unittest.TestCase):

    def setUp(self):
        self.session = SessionManager.get_session(agent_id="test-agent", source="discord", channel="session1")

    def test_on_llm_start_appends_to_session(self):
        handler = LoggingHandler(session=self.session, role="user", human_message="hello")
        handler.manager = MagicMock()
        
        handler.on_llm_start(None, ["Prompt 1"])
        
        handler.manager.append_message.assert_called_once_with("test-agent:discord:session1", "user", "hello")

    def test_on_llm_end_appends_to_session(self):
        handler = LoggingHandler(session=self.session)
        handler.manager = MagicMock()
        
        mock_response = MagicMock()
        mock_generation = MagicMock()
        mock_generation.text = "AI Reply"
        mock_response.generations = [[mock_generation]]
        
        handler.on_llm_end(mock_response)
        
        handler.manager.append_message.assert_called_once_with("test-agent:discord:session1", "ai", "AI Reply")

    def test_on_tool_start_appends_to_session(self):
        handler = LoggingHandler(session=self.session)
        handler.manager = MagicMock()
        
        handler.on_tool_start({"name": "MyTool"}, "input_args")
        
        handler.manager.append_message.assert_called_once_with("test-agent:discord:session1", "system", "Tool MyTool:input_args")

    def test_on_tool_start_extracts_extra_info(self):
        handler = LoggingHandler(session=self.session)
        handler.manager = MagicMock()
        
        input_str = "{'action': 'create', 'path': '/tmp', 'skill_id': 'skill_123'}"
        handler.on_tool_start({"name": "MyTool"}, input_str)
        
        handler.manager.append_message.assert_called_once_with(
            "test-agent:discord:session1", 
            "system", 
            "Tool MyTool [action: create, path: /tmp, skill_id: skill_123]:{'action': 'create', 'path': '/tmp', 'skill_id': 'skill_123'}"
        )

    def test_on_tool_start_filesystem_instructions_multiple(self):
        handler = LoggingHandler(session=self.session)
        handler.manager = MagicMock()
        
        input_str = "{'instructions': [{'action': 'ls', 'path': '{directory1}'}, {'action': 'read', 'path': '{file2}'}]}"
        handler.on_tool_start({"name": "filesystem"}, input_str)
        
        handler.manager.append_message.assert_called_once_with(
            "test-agent:discord:session1",
            "system",
            'Tool filesystem [action "ls" on {directory1}, "read" on {file2}]:{\'instructions\': [{\'action\': \'ls\', \'path\': \'{directory1}\'}, {\'action\': \'read\', \'path\': \'{file2}\'}]}'
        )

    def test_on_tool_start_filesystem_instructions_single(self):
        handler = LoggingHandler(session=self.session)
        handler.manager = MagicMock()
        
        input_str = "{'agent_id': 'main', 'instructions': [{'action': 'ls', 'path': '/tmp'}]}"
        handler.on_tool_start({"name": "filesystem"}, input_str)
        
        handler.manager.append_message.assert_called_once_with(
            "test-agent:discord:session1",
            "system",
            'Tool filesystem [action "ls" on /tmp]:{\'agent_id\': \'main\', \'instructions\': [{\'action\': \'ls\', \'path\': \'/tmp\'}]}'
        )

    def test_on_tool_start_filesystem_json_input_str(self):
        handler = LoggingHandler(session=self.session)
        handler.manager = MagicMock()
        
        input_str = '{"agent_id": "main", "instructions": [{"action": "ls", "path": "{directory1}"}, {"action": "read", "path": "{file2}"}]}'
        handler.on_tool_start({"name": "filesystem"}, input_str)
        
        handler.manager.append_message.assert_called_once_with(
            "test-agent:discord:session1",
            "system",
            'Tool filesystem [action "ls" on {directory1}, "read" on {file2}]:' + input_str
        )

    def test_on_tool_start_filesystem_dict_input(self):
        handler = LoggingHandler(session=self.session)
        handler.manager = MagicMock()
        
        input_dict = {"agent_id": "main", "instructions": [{"action": "ls", "path": "/var/log"}]}
        handler.on_tool_start({"name": "filesystem"}, input_dict)
        
        handler.manager.append_message.assert_called_once_with(
            "test-agent:discord:session1",
            "system",
            'Tool filesystem [action "ls" on /var/log]:' + str(input_dict)
        )

    def test_on_tool_start_with_agent_id_print(self):
        from unittest.mock import patch
        sess = SessionManager.get_session(agent_id="graph-worker", source="discord", channel="session1")
        handler = LoggingHandler(session=sess)
        handler.manager = MagicMock()
        
        input_str = "{'instructions': [{'action': 'ls', 'path': '{file}'}]}"
        with patch("builtins.print") as mock_print:
            handler.on_tool_start({"name": "filesystem"}, input_str)
            mock_print.assert_called_once_with('[Agent:graph-worker] Tool use: filesystem [action "ls" on {file}]')

    def test_on_tool_start_with_contextvar_agent_id(self):
        from unittest.mock import patch
        from core.agent.job_manager import current_session_identifier
        handler = LoggingHandler(session=self.session)
        handler.agent_id = None
        handler.manager = MagicMock()
        
        sess = SessionManager.get_session(agent_id="graph-worker", source="discord", channel="general")
        token = current_session_identifier.set(sess)
        try:
            input_str = "{'instructions': [{'action': 'ls', 'path': '{file}'}]}"
            with patch("builtins.print") as mock_print:
                handler.on_tool_start({"name": "filesystem"}, input_str)
                mock_print.assert_called_once_with('[Agent:graph-worker] Tool use: filesystem [action "ls" on {file}]')
        finally:
            current_session_identifier.reset(token)

    def test_on_tool_start_with_input_agent_id(self):
        from unittest.mock import patch
        handler = LoggingHandler(session=self.session)
        handler.agent_id = None
        handler.manager = MagicMock()
        
        input_str = "{'agent_id': 'graph-worker', 'instructions': [{'action': 'ls', 'path': '{file}'}]}"
        with patch("builtins.print") as mock_print:
            handler.on_tool_start({"name": "filesystem"}, input_str)
            mock_print.assert_called_once_with('[Agent:graph-worker] Tool use: filesystem [action "ls" on {file}]')

    def test_on_tool_start_agent_call_with_agent_print(self):
        from unittest.mock import patch
        sess = SessionManager.get_session(agent_id="software-planner", source="discord", channel="session1")
        handler = LoggingHandler(session=sess)
        handler.manager = MagicMock()
        
        input_str = "{'agent_id': 'graph-worker', 'prompt': 'build feature', 'channel': 'coding-pipeline'}"
        with patch("builtins.print") as mock_print:
            handler.on_tool_start({"name": "agent_call"}, input_str)
            mock_print.assert_called_once_with('[Agent:software-planner] Tool use: agent_call [agent_id: graph-worker]')

    def test_on_tool_start_graph_call_with_agent_print(self):
        from unittest.mock import patch
        sess = SessionManager.get_session(agent_id="software-planner", source="discord", channel="session1")
        handler = LoggingHandler(session=sess)
        handler.manager = MagicMock()
        
        input_str = "{'graph_name': 'coding', 'query': 'build feature'}"
        with patch("builtins.print") as mock_print:
            handler.on_tool_start({"name": "graph_call"}, input_str)
            mock_print.assert_called_once_with('[Agent:software-planner] Tool use: graph_call [graph_id: coding]')

    def test_on_tool_start_other_tools_sweep(self):
        handler = LoggingHandler(session=self.session)
        handler.manager = MagicMock()
        
        # agent_call tool
        handler.on_tool_start({"name": "agent_call"}, "{'agent_id': 'graph-worker', 'prompt': 'build feature'}")
        handler.manager.append_message.assert_called_with(
            "test-agent:discord:session1", "system", "Tool agent_call [agent_id: graph-worker]:{'agent_id': 'graph-worker', 'prompt': 'build feature'}"
        )

        # graph_call tool
        handler.on_tool_start({"name": "graph_call"}, "{'graph_name': 'coding', 'query': 'build feature'}")
        handler.manager.append_message.assert_called_with(
            "test-agent:discord:session1", "system", "Tool graph_call [graph_id: coding]:{'graph_name': 'coding', 'query': 'build feature'}"
        )

        # git tool
        handler.on_tool_start({"name": "git"}, "{'command': 'status', 'path': '/repo'}")
        handler.manager.append_message.assert_called_with(
            "test-agent:discord:session1", "system", "Tool git [path: /repo]:{'command': 'status', 'path': '/repo'}"
        )
        
        # load_skill tool
        handler.on_tool_start({"name": "load_skill"}, "{'skill_id': 'code_search', 'agent_id': 'main'}")
        handler.manager.append_message.assert_called_with(
            "test-agent:discord:session1", "system", "Tool load_skill [skill_id: code_search]:{'skill_id': 'code_search', 'agent_id': 'main'}"
        )
        
        # task_query tool
        handler.on_tool_start({"name": "task_query"}, "{'action': 'search', 'query': 'fix bug'}")
        handler.manager.append_message.assert_called_with(
            "test-agent:discord:session1", "system", "Tool task_query [action: search]:{'action': 'search', 'query': 'fix bug'}"
        )
        
        # web_search tool (no action/path/skill_id/instructions)
        handler.on_tool_start({"name": "web_search"}, "{'query': 'python langgraph'}")
        handler.manager.append_message.assert_called_with(
            "test-agent:discord:session1", "system", "Tool web_search:{'query': 'python langgraph'}"
        )

    def test_on_tool_end_appends_to_session(self):
        handler = LoggingHandler(session=self.session)
        handler.manager = MagicMock()
        
        mock_output = MagicMock()
        mock_output.content = "Tool result"
        
        handler.on_tool_end(mock_output)
        
        handler.manager.append_message.assert_called_once_with("test-agent:discord:session1", "system", "Tool Output: Tool result")

    def test_on_tool_end_string_output_appends_to_session(self):
        handler = LoggingHandler(session=self.session)
        handler.manager = MagicMock()
        
        handler.on_tool_end("Simple string output")
        
        handler.manager.append_message.assert_called_once_with("test-agent:discord:session1", "system", "Tool Output: Simple string output")

    def test_on_llm_end_extracts_token_usage(self):
        handler = LoggingHandler(session=self.session)
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
        handler = LoggingHandler(session=self.session)
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
            "test-agent:discord:session1", "gemini-pro", 100, 50, 20.0, 1.234
        )

    def test_on_llm_start_and_end_tracks_execution_time(self):
        handler = LoggingHandler(session=self.session)
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
        handler = LoggingHandler(session=self.session, role="user", human_message=msg_list)
        handler.manager = MagicMock()
        
        handler.on_llm_start(None, ["Prompt 1"])
        
        import json
        handler.manager.append_message.assert_called_once_with("test-agent:discord:session1", "user", json.dumps(msg_list))

    def test_on_llm_start_does_not_duplicate_on_subsequent_calls(self):
        handler = LoggingHandler(session=self.session, role="user", human_message="hello")
        handler.manager = MagicMock()
        
        handler.on_llm_start(None, ["Prompt 1"])
        handler.on_llm_start(None, ["Prompt 2"])
        
        handler.manager.append_message.assert_called_once_with("test-agent:discord:session1", "user", "hello")

    def test_on_llm_end_handles_list_content(self):
        handler = LoggingHandler(session=self.session)
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
        handler.manager.append_message.assert_called_once_with("test-agent:discord:session1", "ai", json.dumps([{"type": "text", "text": "AI reply list"}]))

    def test_on_tool_start_and_end_tracks_execution_time(self):
        handler = LoggingHandler(session=self.session)
        handler.manager = MagicMock()
        
        handler.on_tool_start({"name": "web_search"}, "query_string", run_id="run_123")
        self.assertIn("run_123", handler.tool_start_times)
        
        mock_output = MagicMock()
        mock_output.content = "Search result output"
        
        handler.on_tool_end(mock_output, run_id="run_123")
        self.assertNotIn("run_123", handler.tool_start_times)
        
        self.assertEqual(handler.manager.append_message.call_count, 2)
        start_call = handler.manager.append_message.call_args_list[0][0]
        self.assertEqual(start_call, ("test-agent:discord:session1", "system", "Tool web_search:query_string"))
        
        end_call = handler.manager.append_message.call_args_list[1][0]
        self.assertEqual(end_call[0], "test-agent:discord:session1")
        self.assertEqual(end_call[1], "system")
        logged_msg = end_call[2]
        self.assertTrue(logged_msg.startswith("Tool Output ["), f"Message '{logged_msg}' should start with 'Tool Output ['")
        self.assertTrue(logged_msg.endswith("s]: Search result output"), f"Message '{logged_msg}' should end with 's]: Search result output'")


class TestFormatToolExtraStr(unittest.TestCase):
    def test_format_filesystem_multiple_instructions_dict(self):
        input_data = {
            "instructions": [
                {"action": "ls", "path": "{directory1}"},
                {"action": "read", "path": "{file2}"}
            ]
        }
        res = format_tool_extra_str(input_data)
        self.assertEqual(res, ' [action "ls" on {directory1}, "read" on {file2}]')

    def test_format_filesystem_multiple_instructions_str(self):
        input_str = "{'instructions': [{'action': 'ls', 'path': '/var/log'}, {'action': 'read', 'path': '/etc/hosts'}]}"
        res = format_tool_extra_str(input_str)
        self.assertEqual(res, ' [action "ls" on /var/log, "read" on /etc/hosts]')

    def test_format_filesystem_single_instruction(self):
        input_data = {"instructions": [{"action": "ls", "path": "/tmp"}]}
        res = format_tool_extra_str(input_data)
        self.assertEqual(res, ' [action "ls" on /tmp]')

    def test_format_filesystem_action_only(self):
        input_data = {"instructions": [{"action": "list_all"}]}
        res = format_tool_extra_str(input_data)
        self.assertEqual(res, ' [action "list_all"]')

    def test_format_filesystem_path_only(self):
        input_data = {"instructions": [{"path": "/tmp/test"}]}
        res = format_tool_extra_str(input_data)
        self.assertEqual(res, ' [action on /tmp/test]')

    def test_format_filesystem_serialized_json_instructions(self):
        input_data = {'instructions': '[{"action": "ls", "path": "/tmp"}]'}
        res = format_tool_extra_str(input_data)
        self.assertEqual(res, ' [action "ls" on /tmp]')

    def test_format_action_path_skill_id(self):
        input_data = {"action": "create", "path": "/tmp", "skill_id": "skill_123"}
        res = format_tool_extra_str(input_data)
        self.assertEqual(res, " [action: create, path: /tmp, skill_id: skill_123]")

    def test_format_path_only(self):
        input_str = "{'command': 'status', 'path': '/repo'}"
        res = format_tool_extra_str(input_str)
        self.assertEqual(res, " [path: /repo]")

    def test_format_skill_id_only(self):
        input_data = {"skill_id": "code_search", "agent_id": "main"}
        res = format_tool_extra_str(input_data)
        self.assertEqual(res, " [skill_id: code_search]")

    def test_format_action_only(self):
        input_str = "{'action': 'search', 'query': 'fix bug'}"
        res = format_tool_extra_str(input_str)
        self.assertEqual(res, " [action: search]")

    def test_format_no_matching_keys(self):
        input_data = {"query": "python langgraph"}
        res = format_tool_extra_str(input_data)
        self.assertEqual(res, "")

    def test_format_graph_name(self):
        input_data = {"graph_name": "coding", "query": "implement feature"}
        res = format_tool_extra_str(input_data)
        self.assertEqual(res, " [graph_id: coding]")

    def test_format_graph_id(self):
        input_str = "{'graph_id': 'content_creation', 'query': 'create post'}"
        res = format_tool_extra_str(input_str)
        self.assertEqual(res, " [graph_id: content_creation]")

    def test_format_subgraph_name(self):
        input_data = {"subgraph_name": "coding", "query": "fix bug"}
        res = format_tool_extra_str(input_data)
        self.assertEqual(res, " [graph_id: coding]")

    def test_format_agent_call_with_tool_name(self):
        input_data = {"agent_id": "graph-worker", "prompt": "run task", "channel": "dev"}
        res = format_tool_extra_str(input_data, tool_name="agent_call")
        self.assertEqual(res, " [agent_id: graph-worker]")

    def test_format_agent_id_standalone(self):
        input_str = "{'agent_id': 'graph-worker', 'prompt': 'run task'}"
        res = format_tool_extra_str(input_str)
        self.assertEqual(res, " [agent_id: graph-worker]")

    def test_format_empty_or_none_or_invalid_inputs(self):
        self.assertEqual(format_tool_extra_str(None), "")
        self.assertEqual(format_tool_extra_str({}), "")
        self.assertEqual(format_tool_extra_str(""), "")
        self.assertEqual(format_tool_extra_str("invalid {string"), "")
        self.assertEqual(format_tool_extra_str(12345), "")


if __name__ == "__main__":
    unittest.main()
