import unittest
import os
import shutil
import tempfile
import sys
import asyncio
from unittest.mock import patch

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer, sanitize_table_name

class TestSqliteCheckpointer(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_memory.db")
        self.checkpointer = SqliteCheckpointer(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_init_creates_db_file(self):
        self.assertTrue(os.path.exists(self.db_path))

    def test_sanitize_table_name(self):
        tbl = sanitize_table_name("thread:123/abc-test.xyz")
        self.assertEqual(tbl, "ctx_thread_123_abc_test_xyz")

    def test_put_and_get_tuple_latest(self):
        config = {"configurable": {"thread_id": "thread1"}}
        checkpoint = {"id": "cp1", "channel_values": {"messages": ["hello"]}}
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
        self.assertIsNotNone(cp_tuple_spec)
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
        
        cp_tuple = self.checkpointer.get_tuple(config)
        self.assertIsNotNone(cp_tuple)
        
        self.checkpointer.delete_thread("thread1")
        cp_tuple_after = self.checkpointer.get_tuple(config)
        self.assertIsNone(cp_tuple_after)

    def test_aput_and_aget_tuple(self):
        config = {"configurable": {"thread_id": "thread2"}}
        checkpoint = {"id": "cp2"}
        metadata = {"step": 2}
        new_versions = {}

        async def run_test():
            return_config = await self.checkpointer.aput(config, checkpoint, metadata, new_versions)
            self.assertEqual(return_config["configurable"]["checkpoint_id"], "cp2")
            cp_tuple = await self.checkpointer.aget_tuple(config)
            self.assertIsNotNone(cp_tuple)
            self.assertEqual(cp_tuple.checkpoint["id"], "cp2")
        
        asyncio.run(run_test())

    def test_aput_writes(self):
        config = {"configurable": {"thread_id": "thread3", "checkpoint_id": "cp3"}}
        writes = [("channel1", "value1")]
        
        async def run_test():
            await self.checkpointer.aput_writes(config, writes, "task1")
            cp_tuple = await self.checkpointer.aget_tuple(config)
            # Tuple doesn't error when pending writes exist
        
        asyncio.run(run_test())

    def test_alist(self):
        config = {"configurable": {"thread_id": "thread4"}}
        
        async def run_test():
            await self.checkpointer.aput(config, {"id": "cp1"}, {"step": 1}, {})
            await self.checkpointer.aput(config, {"id": "cp2"}, {"step": 2}, {})
            
            cps = []
            async for cp in self.checkpointer.alist(config):
                cps.append(cp)
            
            self.assertEqual(len(cps), 2)
            self.assertEqual(cps[0].checkpoint["id"], "cp2")
            
        asyncio.run(run_test())

    def test_blob_compression_and_decompression(self):
        import pickle
        import zlib

        data = {"large_text": "A" * 10000}
        serialized = self.checkpointer._serialize_blob(data)
        self.assertTrue(len(serialized) < len(pickle.dumps(data)))
        
        # Test modern decompression
        recovered = self.checkpointer._deserialize_blob(serialized)
        self.assertEqual(recovered, data)

        # Test legacy uncompressed blob fallback
        legacy_blob = pickle.dumps(data)
        legacy_recovered = self.checkpointer._deserialize_blob(legacy_blob)
        self.assertEqual(legacy_recovered, data)

    def test_checkpoint_pruning(self):
        config = {"configurable": {"thread_id": "prune_thread"}}
        
        # Put 15 checkpoints
        for i in range(15):
            self.checkpointer.put(config, {"id": f"cp_{i}"}, {"step": i}, {})

        # Query direct SQLite row count for checkpoints
        with self.checkpointer._get_connection() as conn:
            cursor = conn.execute('SELECT count(*) as cnt FROM "ctx_prune_thread" WHERE entry_type = \'checkpoint\'')
            cnt = cursor.fetchone()["cnt"]
            self.assertEqual(cnt, 10)

        # Verify that get_tuple still returns the latest
        latest = self.checkpointer.get_tuple(config)
        self.assertEqual(latest.checkpoint["id"], "cp_14")

    def test_vacuum(self):
        # Ensure vacuum method executes without errors
        self.checkpointer.vacuum()

    def test_archive_thread(self):
        config = {"configurable": {"thread_id": "thread_to_archive"}}
        self.checkpointer.put(config, {"id": "cp1"}, {"step": 1}, {})
        
        self.assertIsNotNone(self.checkpointer.get_tuple(config))
        res = self.checkpointer.archive_thread("thread_to_archive")
        self.assertIn("archived to table ctx_thread_to_archive_archived_", res)
        self.assertIsNone(self.checkpointer.get_tuple(config))

    def test_archive_all(self):
        self.checkpointer.put({"configurable": {"thread_id": "t1"}}, {"id": "cp1"}, {"step": 1}, {})
        self.checkpointer.put({"configurable": {"thread_id": "t2"}}, {"id": "cp2"}, {"step": 1}, {})
        
        res = self.checkpointer.archive_all()
        self.assertIn("Archived ctx_t1 to ctx_t1_archived_", res)
        self.assertIn("Archived ctx_t2 to ctx_t2_archived_", res)
        
        self.assertIsNone(self.checkpointer.get_tuple({"configurable": {"thread_id": "t1"}}))
        self.assertIsNone(self.checkpointer.get_tuple({"configurable": {"thread_id": "t2"}}))

    def test_sanitize_dangling_tool_calls_on_get_tuple(self):
        from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
        
        config = {"configurable": {"thread_id": "dangling_tool_thread"}}
        dangling_tc = {"name": "graph_call", "args": {"graph_name": "content_creation"}, "id": "call_abc_123"}
        
        # Checkpoint ends with an AIMessage requesting a tool call (no ToolMessage was emitted before interrupt/crash)
        messages = [
            HumanMessage(content="Create video"),
            AIMessage(content="", tool_calls=[dangling_tc])
        ]
        checkpoint = {
            "id": "cp_dangling",
            "channel_values": {
                "messages": messages
            }
        }
        
        self.checkpointer.put(config, checkpoint, {"step": 1}, {})
        
        # Retrieve checkpoint via get_tuple
        cp_tuple = self.checkpointer.get_tuple(config)
        self.assertIsNotNone(cp_tuple)
        retrieved_messages = cp_tuple.checkpoint["channel_values"]["messages"]
        
        # Verify a synthetic ToolMessage was appended to satisfy chat history validation invariants
        self.assertEqual(len(retrieved_messages), 3)
        self.assertEqual(retrieved_messages[0].content, "Create video")
        self.assertEqual(retrieved_messages[1].tool_calls[0]["id"], "call_abc_123")
        self.assertIsInstance(retrieved_messages[2], ToolMessage)
        self.assertEqual(retrieved_messages[2].tool_call_id, "call_abc_123")
        self.assertIn("interrupted", retrieved_messages[2].content)

    def test_sanitize_interrupted_tool_calls_between_messages(self):
        from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
        
        config = {"configurable": {"thread_id": "interrupted_tool_thread"}}
        dangling_tc = {"name": "search_tool", "args": {}, "id": "call_search_456"}
        
        # AIMessage with tool call immediately followed by a new HumanMessage without intermediate ToolMessage
        messages = [
            HumanMessage(content="Step 1"),
            AIMessage(content="", tool_calls=[dangling_tc]),
            HumanMessage(content="Step 2 user interjection")
        ]
        checkpoint = {
            "id": "cp_interrupted",
            "channel_values": {
                "messages": messages
            }
        }
        
        self.checkpointer.put(config, checkpoint, {"step": 1}, {})
        
        cp_tuple = self.checkpointer.get_tuple(config)
        self.assertIsNotNone(cp_tuple)
        retrieved_messages = cp_tuple.checkpoint["channel_values"]["messages"]
        
        # Must be 4 messages: Human -> AI (with tc) -> Synthetic ToolMessage -> Human
        self.assertEqual(len(retrieved_messages), 4)
        self.assertIsInstance(retrieved_messages[1], AIMessage)
        self.assertIsInstance(retrieved_messages[2], ToolMessage)
        self.assertEqual(retrieved_messages[2].tool_call_id, "call_search_456")
        self.assertEqual(retrieved_messages[3].content, "Step 2 user interjection")

    def test_sanitize_preserves_already_valid_tool_messages(self):
        from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
        
        config = {"configurable": {"thread_id": "valid_tool_thread"}}
        tc = {"name": "calculator", "args": {}, "id": "call_calc_789"}
        
        # Already valid chat history
        messages = [
            HumanMessage(content="Calculate"),
            AIMessage(content="", tool_calls=[tc]),
            ToolMessage(content="42", tool_call_id="call_calc_789"),
            AIMessage(content="The answer is 42.")
        ]
        checkpoint = {
            "id": "cp_valid",
            "channel_values": {
                "messages": messages
            }
        }
        
        self.checkpointer.put(config, checkpoint, {"step": 1}, {})
        
        cp_tuple = self.checkpointer.get_tuple(config)
        self.assertIsNotNone(cp_tuple)
        retrieved_messages = cp_tuple.checkpoint["channel_values"]["messages"]
        
        # Exactly 4 messages preserved without modification
        self.assertEqual(len(retrieved_messages), 4)
        self.assertEqual(retrieved_messages[2].content, "42")
        self.assertEqual(retrieved_messages[3].content, "The answer is 42.")

if __name__ == "__main__":
    unittest.main()
