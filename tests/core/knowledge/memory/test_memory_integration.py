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

    def test_channel_and_thread_isolation_end_to_end(self):
        """
        Validates the exact scenario:
        (main thread A)
        message A1
        message A2
        new thread B1 (main thread sees this one) -> message B2 -> message B3
        message A3

        Main channel should see: A1, A2, B1, A3.
        Thread B should see: B1, B2, B3.
        Delegated subagent checkpoints for channel and thread must remain completely isolated.
        """
        main_sess = "main:discord:topic-research"
        thread_sess = "main:discord:topic-research:1541110915540324533"
        tool_main_sess = "topic-researcher:tool:topic-research"
        tool_thread_sess = "topic-researcher:tool:topic-research:1541110915540324533"

        # 1. Main thread A: A1, A2
        self.store.append_message(main_sess, "user", "A1: Introduce cognitive load theory")
        self.store.append_message(main_sess, "ai", "AI: Explaining cognitive load theory...")
        self.store.append_message(tool_main_sess, "user", "A1: Introduce cognitive load theory")
        self.store.append_message(tool_main_sess, "ai", "AI: Explaining cognitive load theory...")

        self.store.append_message(main_sess, "user", "A2: Compare extraneous vs germane load")
        self.store.append_message(main_sess, "ai", "AI: Comparing load types...")
        self.store.append_message(tool_main_sess, "user", "A2: Compare extraneous vs germane load")
        self.store.append_message(tool_main_sess, "ai", "AI: Comparing load types...")

        # 2. New thread B started from B1:
        # Main channel sees B1 (starter message posted in channel)
        self.store.append_message(main_sess, "user", "B1: Let's investigate swarm coordination")
        self.store.append_message(main_sess, "ai", "AI: Creating thread for swarm coordination...")

        # Thread B receives B1 as starter and replies
        self.store.append_message(thread_sess, "user", "B1: Let's investigate swarm coordination")
        self.store.append_message(thread_sess, "ai", "AI: Exploring swarm coordination...")
        self.store.append_message(tool_thread_sess, "user", "B1: Let's investigate swarm coordination")
        self.store.append_message(tool_thread_sess, "ai", "AI: Exploring swarm coordination...")

        # Thread B receives B2, B3
        self.store.append_message(thread_sess, "user", "B2: Explain git worktree isolation")
        self.store.append_message(thread_sess, "ai", "AI: Explaining git worktree...")
        self.store.append_message(tool_thread_sess, "user", "B2: Explain git worktree isolation")
        self.store.append_message(tool_thread_sess, "ai", "AI: Explaining git worktree...")

        self.store.append_message(thread_sess, "user", "B3: How to test collision resolution")
        self.store.append_message(thread_sess, "ai", "AI: Detailing collision tests...")
        self.store.append_message(tool_thread_sess, "user", "B3: How to test collision resolution")
        self.store.append_message(tool_thread_sess, "ai", "AI: Detailing collision tests...")

        # 3. Main channel receives A3
        self.store.append_message(main_sess, "user", "A3: Back to deliberate practice")
        self.store.append_message(main_sess, "ai", "AI: Explaining deliberate practice...")
        self.store.append_message(tool_main_sess, "user", "A3: Back to deliberate practice")
        self.store.append_message(tool_main_sess, "ai", "AI: Explaining deliberate practice...")

        # 4. Verify Main Channel Messages: [A1, A2, B1, A3]
        main_user_msgs = [e["message"] for e in self.store.load_history(main_sess) if e["from"] == "user"]
        self.assertEqual(main_user_msgs, [
            "A1: Introduce cognitive load theory",
            "A2: Compare extraneous vs germane load",
            "B1: Let's investigate swarm coordination",
            "A3: Back to deliberate practice"
        ])

        # 5. Verify Thread B Messages: [B1, B2, B3] (Does NOT contain A1, A2, or A3)
        thread_user_msgs = [e["message"] for e in self.store.load_history(thread_sess) if e["from"] == "user"]
        self.assertEqual(thread_user_msgs, [
            "B1: Let's investigate swarm coordination",
            "B2: Explain git worktree isolation",
            "B3: How to test collision resolution"
        ])

        # 6. Verify Subagent Main Tool Session: [A1, A2, A3] (Does NOT contain B2 or B3)
        tool_main_user_msgs = [e["message"] for e in self.store.load_history(tool_main_sess) if e["from"] == "user"]
        self.assertEqual(tool_main_user_msgs, [
            "A1: Introduce cognitive load theory",
            "A2: Compare extraneous vs germane load",
            "A3: Back to deliberate practice"
        ])

        # 7. Verify Subagent Thread Tool Session: [B1, B2, B3] (Does NOT contain A1, A2, A3)
        tool_thread_user_msgs = [e["message"] for e in self.store.load_history(tool_thread_sess) if e["from"] == "user"]
        self.assertEqual(tool_thread_user_msgs, [
            "B1: Let's investigate swarm coordination",
            "B2: Explain git worktree isolation",
            "B3: How to test collision resolution"
        ])


if __name__ == "__main__":
    unittest.main()
