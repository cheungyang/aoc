import unittest
import os
import shutil
import tempfile
from unittest.mock import patch, MagicMock
import sys
import json

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.memory.flat_file_session_store import FlatFileSessionStore
from core.memory import flat_file_checkpointer

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

        # Verify file content is JSONL
        file_path = self.store.get_file_path(session_id)
        with open(file_path, "r") as f:
            lines = f.readlines()
            self.assertEqual(len(lines), 2)
            for line in lines:
                parsed = json.loads(line)
                self.assertIn("from", parsed)

    @patch('core.memory.flat_file_checkpointer.FlatFileCheckpointer')
    def test_archive_session(self, mock_saver_class):
        # Setup mock saver
        mock_saver = mock_saver_class.return_value
        
        session_id = "session1"
        self.store.append_message(session_id, "user", "hello")
        
        # Archive
        result = self.store.archive_session(session_id)
        self.assertIn("archived to archive", result)

        # Verify old file is gone
        file_path = self.store.get_file_path(session_id)
        self.assertFalse(os.path.exists(file_path))

        # Verify archive file exists
        archive_dir = os.path.join(self.test_dir, "archive")
        self.assertTrue(os.path.exists(archive_dir))
        self.assertTrue(len(os.listdir(archive_dir)) > 0)
        
        # Verify delete_thread was called on mock saver with session_id
        mock_saver.delete_thread.assert_called_once_with(session_id)

    def test_append_token_usage(self):
        session_id = "session1"
        self.store.append_token_usage(session_id, "gemini-pro", 100, 50, 20.0)
        
        safe_id = session_id.replace(":", "_").replace("/", "_")
        token_file = os.path.join(self.test_dir, f"token_{safe_id}.jsonl")
        
        self.assertTrue(os.path.exists(token_file))
        
        with open(token_file, "r") as f:
            lines = f.readlines()
            self.assertEqual(len(lines), 1)
            parsed = json.loads(lines[0])
            self.assertEqual(parsed["model"], "gemini-pro")
            self.assertEqual(parsed["input_token"], 100)
            self.assertEqual(parsed["output_token"], 50)
            self.assertEqual(parsed["cached_token"], 20.0)

    @patch('core.memory.flat_file_checkpointer.FlatFileCheckpointer')
    def test_archive_session_with_token_file(self, mock_saver_class):
        mock_saver = mock_saver_class.return_value
        
        session_id = "session1"
        self.store.append_message(session_id, "user", "hello")
        self.store.append_token_usage(session_id, "gemini-pro", 100, 50, 20.0)
        
        # Archive
        result = self.store.archive_session(session_id)
        self.assertIn("archived to archive", result)
        self.assertIn("and token file to archive", result)
        
        # Verify old files are gone
        file_path = self.store.get_file_path(session_id)
        self.assertFalse(os.path.exists(file_path))
        
        safe_id = session_id.replace(":", "_").replace("/", "_")
        token_file = os.path.join(self.test_dir, f"token_{safe_id}.jsonl")
        self.assertFalse(os.path.exists(token_file))
        
        # Verify archive files exist
        archive_dir = os.path.join(self.test_dir, "archive")
        self.assertTrue(os.path.exists(archive_dir))
        files = os.listdir(archive_dir)
        self.assertEqual(len(files), 2)
        self.assertTrue(any(f.startswith(f"{safe_id}_") for f in files))
        self.assertTrue(any(f.startswith(f"token_{safe_id}_") for f in files))

if __name__ == "__main__":
    unittest.main()
