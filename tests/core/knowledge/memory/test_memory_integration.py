import unittest
import os
import shutil
import tempfile
import sys
import time

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
from core.knowledge.memory.sqlite_session_store import SqliteSessionStore, sanitize_table_name
from core.agent.session_manager import SessionManager
from core.agent.logging_handler import LoggingHandler

class TestMemoryIntegration(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "memory.db")
        self.checkpointer = SqliteCheckpointer(db_path=self.db_path)
        self.store = SqliteSessionStore(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_full_session_lifecycle(self):
        session_id = "main:discord:general"
        
        # 1. Simulate LoggingHandler logging user message
        self.store.append_message(session_id, "user", "What is the weather?")
        
        # 2. Simulate tool execution
        self.store.append_message(session_id, "system", "Tool web_search:{'query': 'weather'}")
        self.store.append_message(session_id, "system", "Tool Output: Sunny, 72F")
        
        # 3. Simulate AI reply
        self.store.append_message(session_id, "ai", "The weather is sunny and 72F.")
        
        # 4. Simulate token usage recording
        self.store.append_token_usage(session_id, "gemini-flash", 1200, 150, 45.5)
        
        # 5. Simulate Checkpointer put
        config = {"configurable": {"thread_id": session_id}}
        checkpoint = {
            "id": "cp_step_1",
            "channel_values": {
                "messages": [
                    {"role": "user", "content": "What is the weather?"},
                    {"role": "ai", "content": "The weather is sunny and 72F."}
                ]
            }
        }
        meta = {"step": 1, "source": "input"}
        self.checkpointer.put(config, checkpoint, meta, {})
        
        # 6. Verify Checkpointer get_tuple retrieves state
        cp_tuple = self.checkpointer.get_tuple(config)
        self.assertIsNotNone(cp_tuple)
        self.assertEqual(cp_tuple.checkpoint["id"], "cp_step_1")
        self.assertEqual(len(cp_tuple.checkpoint["channel_values"]["messages"]), 2)
        
        # 7. Verify History & Token History
        history = self.store.load_history(session_id)
        self.assertEqual(len(history), 4)
        self.assertEqual(history[0]["from"], "user")
        self.assertEqual(history[1]["from"], "system")
        self.assertEqual(history[2]["from"], "system")
        self.assertEqual(history[3]["from"], "ai")
        
        token_hist = self.store.load_token_history(session_id)
        self.assertEqual(len(token_hist), 1)
        self.assertEqual(token_hist[0]["input_token"], 1200)
        self.assertEqual(token_hist[0]["output_token"], 150)
        self.assertEqual(token_hist[0]["cached_token"], 45.5)
        
        # 8. Test [new] command Archive Renaming
        archive_res = self.store.archive_session(session_id)
        self.assertIn("archived to table ctx_main_discord_general_archived_", archive_res)
        
        # Active session should now be blank
        self.assertEqual(len(self.store.load_history(session_id)), 0)
        self.assertEqual(len(self.store.load_token_history(session_id)), 0)
        self.assertIsNone(self.checkpointer.get_tuple(config))
        
        # 9. Next turn starts a clean session
        self.store.append_message(session_id, "user", "Hello again, fresh session!")
        new_history = self.store.load_history(session_id)
        self.assertEqual(len(new_history), 1)
        self.assertEqual(new_history[0]["message"], "Hello again, fresh session!")
        
        # 10. Check that archived table still preserves old history
        with self.store._get_connection() as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ctx_main_discord_general_archived_%'")
            archived_tables = cursor.fetchall()
            self.assertEqual(len(archived_tables), 1)
            arch_table_name = archived_tables[0]["name"]
            
            cursor2 = conn.execute(f'SELECT count(*) as count FROM "{arch_table_name}" WHERE entry_type = \'message\'')
            self.assertEqual(cursor2.fetchone()["count"], 4)

    def test_new_and_newall_archive_graph_sqlite_states(self):
        session_id = "main:discord:general"
        
        # 1. Simulate agent session active
        self.store.append_message(session_id, "user", "Run content creation")
        
        # 2. Simulate graph execution in this channel ("general")
        graph_config_general = {"configurable": {"thread_id": "graph:content_creation:general"}}
        self.checkpointer.put(graph_config_general, {"id": "cp_gen_1", "channel_values": {"topic": "scene1"}}, {"step": 1}, {})
        
        # 3. Simulate another graph execution in a different channel ("dev")
        graph_config_dev = {"configurable": {"thread_id": "graph:content_creation:dev"}}
        self.checkpointer.put(graph_config_dev, {"id": "cp_dev_1", "channel_values": {"topic": "scene2"}}, {"step": 1}, {})

        # Verify all 3 tables are active
        active = self.store.list_active_sessions()
        self.assertIn("ctx_main_discord_general", active)
        self.assertIn("ctx_graph_content_creation_general", active)
        self.assertIn("ctx_graph_content_creation_dev", active)

        # 4. Execute [new] for "main:discord:general"
        archive_res = self.store.archive_session(session_id)
        self.assertIn("ctx_main_discord_general_archived_", archive_res)
        self.assertIn("Archived ctx_graph_content_creation_general to ctx_graph_content_creation_general_archived_", archive_res)

        # 5. Verify that graph state in "general" is cleared, but "dev" remains active
        self.assertIsNone(self.checkpointer.get_tuple(graph_config_general))
        self.assertIsNotNone(self.checkpointer.get_tuple(graph_config_dev))

        # 6. Execute [newall] to archive all remaining graph states
        archive_all_res = self.store.archive_all_sessions()
        self.assertIn("Archived ctx_graph_content_creation_dev to ctx_graph_content_creation_dev_archived_", archive_all_res)

        # Verify all active states are empty
        self.assertEqual(len(self.store.list_active_sessions()), 0)
        self.assertIsNone(self.checkpointer.get_tuple(graph_config_dev))

if __name__ == "__main__":
    unittest.main()
