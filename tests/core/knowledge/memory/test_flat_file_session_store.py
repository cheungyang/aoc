import unittest
import os
import shutil
import tempfile
import sys
import json

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from core.knowledge.memory.flat_file_session_store import FlatFileSessionStore

class TestFlatFileSessionStore(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.store = FlatFileSessionStore(sessions_dir=self.test_dir)

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

    def test_archive_session(self):
        session_id = "session1"
        self.store.append_message(session_id, "user", "hello")
        
        # Archive
        result = self.store.archive_session(session_id)
        self.assertIn("archived to table ctx_session1_archived_", result)

        # Active history should now be empty
        history = self.store.load_history(session_id)
        self.assertEqual(len(history), 0)

    def test_append_token_usage(self):
        session_id = "session1"
        self.store.append_token_usage(session_id, "gemini-pro", 100, 50, 20.0)
        
        tokens = self.store.load_token_history(session_id)
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0]["model"], "gemini-pro")
        self.assertEqual(tokens[0]["input_token"], 100)
        self.assertEqual(tokens[0]["output_token"], 50)
        self.assertEqual(tokens[0]["cached_token"], 20.0)

    def test_archive_session_with_token_data(self):
        session_id = "session1"
        self.store.append_message(session_id, "user", "hello")
        self.store.append_token_usage(session_id, "gemini-pro", 100, 50, 20.0)
        
        # Archive
        result = self.store.archive_session(session_id)
        self.assertIn("archived to table ctx_session1_archived_", result)
        
        # Active history should be empty
        history = self.store.load_history(session_id)
        self.assertEqual(len(history), 0)
        tokens = self.store.load_token_history(session_id)
        self.assertEqual(len(tokens), 0)

if __name__ == "__main__":
    unittest.main()
