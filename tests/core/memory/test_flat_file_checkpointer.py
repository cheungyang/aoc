import unittest
import os
import shutil
import tempfile
import sys
from unittest.mock import patch

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.memory.flat_file_checkpointer import FlatFileCheckpointer

class TestFlatFileCheckpointer(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.checkpointer = FlatFileCheckpointer(directory=self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_init_creates_dir(self):
        self.assertTrue(os.path.exists(self.test_dir))

    def test_get_file_path(self):
        path = self.checkpointer._get_file_path("thread:123/abc")
        self.assertIn("thread_123_abc.pkl", path)

    def test_put_and_get_tuple_latest(self):
        config = {"configurable": {"thread_id": "thread1"}}
        checkpoint = {"id": "cp1"}
        metadata = {"step": 1}
        new_versions = {}

        # Put
        return_config = self.checkpointer.put(config, checkpoint, metadata, new_versions)
        self.assertEqual(return_config["configurable"]["thread_id"], "thread1")
        self.assertEqual(return_config["configurable"]["checkpoint_id"], "cp1")

        # Get Tuple Latest
        cp_tuple = self.checkpointer.get_tuple(config)
        self.assertIsNotNone(cp_tuple)
        self.assertEqual(cp_tuple.config["configurable"]["checkpoint_id"], "cp1")
        self.assertEqual(cp_tuple.checkpoint["id"], "cp1")
        self.assertEqual(cp_tuple.metadata["step"], 1)

    def test_put_and_get_tuple_specific(self):
        config = {"configurable": {"thread_id": "thread1"}}
        
        self.checkpointer.put(config, {"id": "cp1"}, {"step": 1}, {})
        self.checkpointer.put(config, {"id": "cp2"}, {"step": 2}, {})

        # Get latest
        cp_tuple = self.checkpointer.get_tuple(config)
        self.assertEqual(cp_tuple.checkpoint["id"], "cp2")

        # Get specific
        specific_config = {"configurable": {"thread_id": "thread1", "checkpoint_id": "cp1"}}
        cp_tuple_spec = self.checkpointer.get_tuple(specific_config)
        self.assertEqual(cp_tuple_spec.checkpoint["id"], "cp1")

    def test_list(self):
        config = {"configurable": {"thread_id": "thread1"}}
        self.checkpointer.put(config, {"id": "cp1"}, {"step": 1}, {})
        self.checkpointer.put(config, {"id": "cp2"}, {"step": 2, "tag": "important"}, {})
        
        # Test list all
        all_cps = list(self.checkpointer.list(config))
        self.assertEqual(len(all_cps), 2)
        self.assertEqual(all_cps[0].checkpoint["id"], "cp2") # Descending order by step

        # Test filter
        filtered = list(self.checkpointer.list(config, filter={"tag": "important"}))
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].checkpoint["id"], "cp2")

    def test_delete_thread(self):
         config = {"configurable": {"thread_id": "thread1"}}
         self.checkpointer.put(config, {"id": "cp1"}, {"step": 1}, {})
         
         path = self.checkpointer._get_file_path("thread1")
         self.assertTrue(os.path.exists(path))
         
         self.checkpointer.delete_thread("thread1")
         self.assertFalse(os.path.exists(path))

# TestMessageStore moved to tests/core/memory/test_flat_file_session_store.py
