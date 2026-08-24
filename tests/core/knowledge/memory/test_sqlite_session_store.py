import unittest
import os
import shutil
import tempfile
import sys
import json

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from core.knowledge.memory.sqlite_session_store import SqliteSessionStore, sanitize_table_name

class TestSqliteSessionStore(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_sessions.db")
        self.store = SqliteSessionStore(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_append_and_load_history(self):
        session_id = "session1"
        self.store.append_message(session_id, "user", "hello")
        self.store.append_message(session_id, "bot", "hi")

        history = self.store.load_history(session_id)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["from"], "user")
        self.assertEqual(history[0]["message"], "hello")
        self.assertEqual(history[1]["from"], "bot")
        self.assertEqual(history[1]["message"], "hi")

    def test_append_and_load_token_usage(self):
        session_id = "session1"
        self.store.append_token_usage(session_id, "gemini-pro", 100, 50, 20.0, execution_time=2.45)

        tokens = self.store.load_token_history(session_id)
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0]["model"], "gemini-pro")
        self.assertEqual(tokens[0]["input_token"], 100)
        self.assertEqual(tokens[0]["output_token"], 50)
        self.assertEqual(tokens[0]["cached_token"], 20.0)
        self.assertEqual(tokens[0]["execution_time"], 2.45)

    def test_archive_session_renames_table(self):
        session_id = "main:discord:general"
        self.store.append_message(session_id, "user", "hello world")
        self.store.append_token_usage(session_id, "gemini-flash", 50, 10, 0.0)

        # Before archive: active table exists
        self.assertIn("ctx_main_discord_general", self.store.list_active_sessions())

        # Archive
        result = self.store.archive_session(session_id)
        self.assertIn("archived to table ctx_main_discord_general_archived_", result)

        # After archive: active table no longer listed as active
        self.assertNotIn("ctx_main_discord_general", self.store.list_active_sessions())

        # Loading history from old session_id returns empty because active table is archived
        history_new = self.store.load_history(session_id)
        self.assertEqual(len(history_new), 0)

        # New message creates fresh active table
        self.store.append_message(session_id, "user", "new conversation turn")
        self.assertIn("ctx_main_discord_general", self.store.list_active_sessions())
        new_hist = self.store.load_history(session_id)
        self.assertEqual(len(new_hist), 1)
        self.assertEqual(new_hist[0]["message"], "new conversation turn")

    def test_archive_all_sessions(self):
        self.store.append_message("session_a", "user", "msg A")
        self.store.append_message("session_b", "user", "msg B")

        active = self.store.list_active_sessions()
        self.assertEqual(len(active), 2)

        res = self.store.archive_all_sessions()
        self.assertIn("Archived", res)

        active_after = self.store.list_active_sessions()
        self.assertEqual(len(active_after), 0)

    def test_archive_session_with_graph_states(self):
        from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
        cp = SqliteCheckpointer(db_path=self.db_path)

        # 1. Main session
        session_id = "main:discord:general"
        self.store.append_message(session_id, "user", "hello general")

        # 2. Graph states in same session / channel
        cp.put({"configurable": {"thread_id": "graph:content_creation:general"}}, {"id": "cp1"}, {"step": 1}, {})
        cp.put({"configurable": {"thread_id": "graph:coding:general"}}, {"id": "cp2"}, {"step": 1}, {})

        # 3. Graph state in another channel
        cp.put({"configurable": {"thread_id": "graph:content_creation:otherchannel"}}, {"id": "cp3"}, {"step": 1}, {})

        active = self.store.list_active_sessions()
        self.assertIn("ctx_main_discord_general", active)
        self.assertIn("ctx_graph_content_creation_general", active)
        self.assertIn("ctx_graph_coding_general", active)
        self.assertIn("ctx_graph_content_creation_otherchannel", active)

        # Archive session "main:discord:general"
        result = self.store.archive_session(session_id)
        self.assertIn("ctx_main_discord_general_archived_", result)
        self.assertIn("Archived ctx_graph_content_creation_general to ctx_graph_content_creation_general_archived_", result)
        self.assertIn("Archived ctx_graph_coding_general to ctx_graph_coding_general_archived_", result)

        # Verify active sessions
        active_after = self.store.list_active_sessions()
        self.assertNotIn("ctx_main_discord_general", active_after)
        self.assertNotIn("ctx_graph_content_creation_general", active_after)
        self.assertNotIn("ctx_graph_coding_general", active_after)
        self.assertIn("ctx_graph_content_creation_otherchannel", active_after)

        # Verify checkpointer tuple returns None for archived threads
        self.assertIsNone(cp.get_tuple({"configurable": {"thread_id": "graph:content_creation:general"}}))
        self.assertIsNone(cp.get_tuple({"configurable": {"thread_id": "graph:coding:general"}}))
        self.assertIsNotNone(cp.get_tuple({"configurable": {"thread_id": "graph:content_creation:otherchannel"}}))

    def test_archive_all_sessions_includes_all_graph_states(self):
        from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
        cp = SqliteCheckpointer(db_path=self.db_path)

        self.store.append_message("main:discord:room1", "user", "msg 1")
        cp.put({"configurable": {"thread_id": "graph:content_creation:room1"}}, {"id": "cp1"}, {"step": 1}, {})
        cp.put({"configurable": {"thread_id": "graph:coding:room2"}}, {"id": "cp2"}, {"step": 1}, {})

        active = self.store.list_active_sessions()
        self.assertEqual(len(active), 3)

        res = self.store.archive_all_sessions()
        self.assertIn("Archived ctx_main_discord_room1 to ctx_main_discord_room1_archived_", res)
        self.assertIn("Archived ctx_graph_content_creation_room1 to ctx_graph_content_creation_room1_archived_", res)
        self.assertIn("Archived ctx_graph_coding_room2 to ctx_graph_coding_room2_archived_", res)

        active_after = self.store.list_active_sessions()
        self.assertEqual(len(active_after), 0)
        self.assertIsNone(cp.get_tuple({"configurable": {"thread_id": "graph:content_creation:room1"}}))
        self.assertIsNone(cp.get_tuple({"configurable": {"thread_id": "graph:coding:room2"}}))

    def test_append_list_and_dict_message(self):
        session_id = "session_structured"
        list_msg = [{"type": "text", "text": "hello"}]
        dict_msg = {"key": "val"}
        self.store.append_message(session_id, "user", list_msg)
        self.store.append_message(session_id, "bot", dict_msg)

        history = self.store.load_history(session_id)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["message"], json.dumps(list_msg))
        self.assertEqual(history[1]["message"], json.dumps(dict_msg))

if __name__ == "__main__":
    unittest.main()
