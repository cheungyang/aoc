import unittest
import os
import sys
from unittest.mock import MagicMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.runtime.logging_handler import LoggingHandler
from core.runtime.session_manager import SessionManager
from tests.helpers import LLM_USAGE as USAGE, llm_result as _llm_result


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
        from core.runtime.execution_context import current_execution_context
        handler = LoggingHandler(session=self.session)
        handler.agent_id = None
        handler.manager = MagicMock()
        
        sess = SessionManager.get_session(agent_id="graph-worker", source="discord", channel="general")
        token = current_execution_context.set(sess)
        try:
            input_str = "{'instructions': [{'action': 'ls', 'path': '{file}'}]}"
            with patch("builtins.print") as mock_print:
                handler.on_tool_start({"name": "filesystem"}, input_str)
                mock_print.assert_called_once_with('[Agent:graph-worker] Tool use: filesystem [action "ls" on {file}]')
        finally:
            current_execution_context.reset(token)

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
            "test-agent:discord:session1", "gemini-pro", 100, 50, 20.0, 1.234, surface="text"
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

    def test_on_tool_start_ignores_different_agent_metadata(self):
        from unittest.mock import patch
        handler_main = LoggingHandler(session=self.session) # agent_id="test-agent"
        handler_main.manager = MagicMock()
        
        with patch("builtins.print") as mock_print:
            handler_main.on_tool_start(
                {"name": "gog"},
                "calendar calendars",
                metadata={"agent_id": "excursion-planner"}
            )
            mock_print.assert_not_called()
            handler_main.manager.append_message.assert_not_called()

    def test_on_tool_start_ignores_different_agent_context(self):
        from unittest.mock import patch
        from core.runtime.execution_context import current_execution_context
        handler_main = LoggingHandler(session=self.session) # agent_id="test-agent"
        handler_main.manager = MagicMock()
        
        sess_sub = SessionManager.get_session(agent_id="excursion-planner", source="tool", channel="session1")
        tok = current_execution_context.set(sess_sub)
        try:
            with patch("builtins.print") as mock_print:
                handler_main.on_tool_start(
                    {"name": "filesystem"},
                    "{'instructions': [{'action': 'append', 'path': '/tmp/log.md'}]}"
                )
                mock_print.assert_not_called()
                handler_main.manager.append_message.assert_not_called()
        finally:
            current_execution_context.reset(tok)

    def test_on_tool_end_ignores_different_agent_metadata(self):
        handler_main = LoggingHandler(session=self.session)
        handler_main.manager = MagicMock()
        
        mock_output = MagicMock()
        mock_output.content = "output from subagent"
        handler_main.on_tool_end(mock_output, metadata={"agent_id": "excursion-planner"})
        handler_main.manager.append_message.assert_not_called()

    def test_on_llm_start_and_end_ignores_different_agent_metadata(self):
        handler_main = LoggingHandler(session=self.session, role="user", human_message="prompt")
        handler_main.manager = MagicMock()
        
        handler_main.on_llm_start(None, ["prompt"], metadata={"agent_id": "excursion-planner"})
        handler_main.manager.append_message.assert_not_called()
        
        mock_response = MagicMock()
        mock_generation = MagicMock()
        mock_generation.text = "Subagent AI reply"
        mock_response.generations = [[mock_generation]]
        handler_main.on_llm_end(mock_response, metadata={"agent_id": "excursion-planner"})
        handler_main.manager.append_message.assert_not_called()

    def test_on_chain_end_ignores_different_agent_metadata(self):
        handler_main = LoggingHandler(session=self.session)
        handler_main.manager = MagicMock()
        handler_main.last_token_usage = {
            "input_tokens": 100,
            "output_tokens": 50,
            "model": "gemini-pro"
        }
        handler_main.on_chain_end({}, metadata={"agent_id": "excursion-planner"})
        handler_main.manager.append_token_usage.assert_not_called()


class TestHandlerModelName(unittest.TestCase):
    """The handler stores the name `extract_model_name` finds on the token row."""

    def setUp(self):
        self.session = SessionManager.get_session(agent_id="test-agent", source="discord", channel="session1")

    def test_handler_records_model_from_message_end_to_end(self):
        handler = LoggingHandler(session=self.session)
        handler.manager = MagicMock()
        handler.on_llm_end(_llm_result(
            usage=USAGE,
            response_metadata={"model_name": "gemini-3.1-pro-preview"},
            llm_output={"prompt_feedback": {"block_reason": 0}},
        ))
        handler.on_chain_end({})
        args = handler.manager.append_token_usage.call_args[0]
        self.assertEqual(args[1], "gemini-3.1-pro-preview")

    def test_handler_records_unknown_without_llm_output(self):
        # Previously 'model' was only set when llm_output was truthy, so a
        # missing llm_output silently dropped the key.
        handler = LoggingHandler(session=self.session)
        handler.manager = MagicMock()
        handler.on_llm_end(_llm_result(usage=USAGE))
        self.assertEqual(handler.last_token_usage["model"], "unknown")

class TestSurface(unittest.TestCase):

    def _logged_surface(self, session):
        handler = LoggingHandler(session=session)
        handler.manager = MagicMock()
        handler.last_token_usage = {"input_tokens": 1, "output_tokens": 1, "model": "m"}
        handler.on_chain_end({})
        return handler.manager.append_token_usage.call_args.kwargs["surface"]

    def test_discord_text_turn_is_text(self):
        s = SessionManager.get_session(agent_id="main", source="discord", channel="general")
        self.assertEqual(self._logged_surface(s), "text")

    def test_voice_turn_is_voice_but_shares_the_text_session(self):
        text = SessionManager.get_session(agent_id="main", source="discord", channel="general")
        voice = SessionManager.get_session(agent_id="main", source="discord", channel="general", surface="voice")
        self.assertEqual(voice.session_id, text.session_id)
        self.assertEqual(self._logged_surface(voice), "voice")

    def test_scheduled_and_tool_sources(self):
        sched = SessionManager.get_session(agent_id="main", source="scheduled", channel="general")
        job = SessionManager.get_session(agent_id="main", source="job")
        tool = SessionManager.get_session(agent_id="sub", source="tool", channel="general")
        self.assertEqual(self._logged_surface(sched), "scheduled")
        self.assertEqual(self._logged_surface(job), "job")
        self.assertEqual(self._logged_surface(tool), "tool")


class TestSurfaceColumnStorage(unittest.TestCase):
    """The surface column lands in memory.db, including tables created before it existed."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "memory.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip(self):
        from core.knowledge.memory.sqlite_session_store import SqliteSessionStore
        store = SqliteSessionStore(db_path=self.db_path)
        store.append_token_usage("main:discord:general", "m", 1, 2, 0.0, 0.5, surface="voice")
        store.append_token_usage("main:discord:general", "m", 1, 2, 0.0, 0.5)
        rows = store.load_token_history("main:discord:general")
        self.assertEqual([r["surface"] for r in rows], ["voice", None])

    def test_migrates_a_legacy_table(self):
        import sqlite3
        from core.knowledge.memory.sqlite_session_store import SqliteSessionStore, sanitize_table_name
        table = sanitize_table_name("main:discord:general")
        conn = sqlite3.connect(self.db_path)
        conn.execute(f"""
            CREATE TABLE "{table}" (
                id INTEGER PRIMARY KEY AUTOINCREMENT, entry_type TEXT NOT NULL,
                checkpoint_id TEXT, step INTEGER DEFAULT -1, data BLOB, metadata TEXT,
                config TEXT, parent_config TEXT, from_role TEXT, message TEXT, model TEXT,
                input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
                cached_tokens REAL DEFAULT 0.0, execution_time REAL DEFAULT 0.0,
                created_at REAL NOT NULL
            )""")
        conn.execute(
            f'INSERT INTO "{table}" (entry_type, model, input_tokens, output_tokens, created_at) '
            "VALUES ('token', 'old', 3, 4, 1.0)"
        )
        conn.commit()
        conn.close()

        store = SqliteSessionStore(db_path=self.db_path)
        # Reading an unmigrated table must not fail.
        self.assertEqual(store.load_token_history("main:discord:general")[0]["surface"], None)
        store.append_token_usage("main:discord:general", "new", 1, 1, 0.0, surface="scheduled")
        rows = store.load_token_history("main:discord:general")
        self.assertEqual([(r["model"], r["surface"]) for r in rows], [("old", None), ("new", "scheduled")])


if __name__ == "__main__":
    unittest.main()
