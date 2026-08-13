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
        self.store.append_token_usage(session_id, "gemini-pro", 100, 50, 20.0)

        tokens = self.store.load_token_history(session_id)
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0]["model"], "gemini-pro")
        self.assertEqual(tokens[0]["input_token"], 100)
        self.assertEqual(tokens[0]["output_token"], 50)
        self.assertEqual(tokens[0]["cached_token"], 20.0)

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
