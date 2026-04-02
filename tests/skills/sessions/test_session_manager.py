import unittest
from unittest.mock import patch, MagicMock
import sys
import os
import json
import tempfile
import shutil

# Inject root workspace to modules path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

import skills.sessions.scripts.session_manager as session_manager

class TestSessionManager(unittest.TestCase):

    def setUp(self):
        # Create isolated temp folder for test files
        self.test_dir = tempfile.mkdtemp()
        self.archive_dir = os.path.join(self.test_dir, "archive")
        
        # Patch the constants calculated in the module
        self.patch_sessions_dir = patch.object(session_manager, 'SESSIONS_DIR', self.test_dir)
        self.patch_archive_dir = patch.object(session_manager, 'ARCHIVE_DIR', self.archive_dir)
        
        self.patch_sessions_dir.start()
        self.patch_archive_dir.start()

    def tearDown(self):
        self.patch_sessions_dir.stop()
        self.patch_archive_dir.stop()
        shutil.rmtree(self.test_dir)

    def test_append_and_load(self):
        session_id = "discord:general"
        session_manager.append_message(session_id, "human", "Hello agent")
        session_manager.append_message(session_id, "agent", "Hello human")
        
        history = session_manager.load_history(session_id)
        
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["from"], "human")
        self.assertEqual(history[0]["message"], "Hello agent")
        self.assertEqual(history[1]["from"], "agent")
        self.assertEqual(history[1]["message"], "Hello human")

    def test_capping_limit_to_50(self):
        session_id = "discord:spam"
        # Seed 60 messages
        for i in range(60):
            session_manager.append_message(session_id, "human", f"msg {i}")
            
        history = session_manager.load_history(session_id)
        
        # Verify result is sliced down to 50 latest
        self.assertEqual(len(history), 50)
        self.assertEqual(history[0]["message"], "msg 10") # 60 - 50 = indices 10 to 59
        self.assertEqual(history[-1]["message"], "msg 59")

    def test_load_empty_non_existent(self):
         history = session_manager.load_history("empty:session")
         self.assertEqual(history, [])

    def test_archive_rotation(self):
        session_id = "discord:temporary"
        session_manager.append_message(session_id, "human", "Temporary context")
        
        # Assert active file exists before rotation
        safe_id = session_id.replace(":", "_")
        active_file_path = os.path.join(self.test_dir, f"{safe_id}.json")
        self.assertTrue(os.path.exists(active_file_path))
        
        # Apply archive rotation
        status = session_manager.archive_session(session_id)
        self.assertIn("archived to archive/", status)
        
        # Active file must be migrated (deleted/moved)
        self.assertFalse(os.path.exists(active_file_path))
        
        # Verify archive migration folder contains files
        self.assertTrue(os.path.exists(self.archive_dir))
        migrated_files = os.listdir(self.archive_dir)
        self.assertEqual(len(migrated_files), 1)
        self.assertTrue(migrated_files[0].startswith(safe_id))

if __name__ == "__main__":
    unittest.main()
