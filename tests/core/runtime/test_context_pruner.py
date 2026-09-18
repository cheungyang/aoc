import unittest
import os
import sys
from unittest.mock import patch, MagicMock, AsyncMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from langchain_core.messages import (
    HumanMessage,
    AIMessage,
    ToolMessage,
    SystemMessage,
)
from core.runtime.context_pruner import ContextPruner
from core.runtime.session_manager import SessionManager
from core.util.message_util import (
    estimate_total_tokens,
)
from core.util.summarize_util import (
    SUMMARY_PREFIX,
    SUMMARY_SUFFIX,
)
from core.util.config import Config


class TestContextPruner(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        Config().reset()
        self.pruner = ContextPruner()
        self.worker_patcher = patch.object(
            ContextPruner,
            '_summarize_with_graph_worker',
            return_value="Mock summarized history"
        )
        self.mock_worker = self.worker_patcher.start()

    def tearDown(self):
        self.worker_patcher.stop()
        Config().reset()

    def test_extract_existing_summary(self):
        summary_text = "User wanted to book flights to Tokyo. Found 3 award seats."
        messages = [
            SystemMessage(content=f"{SUMMARY_PREFIX}\n{summary_text}\n{SUMMARY_SUFFIX}"),
            HumanMessage(content="Now look for hotels."),
            AIMessage(content="Searching hotels now.")
        ]
        extracted, clean = self.pruner._extract_existing_summary(messages)
        self.assertEqual(extracted, summary_text)
        self.assertEqual(len(clean), 2)
        self.assertIsInstance(clean[0], HumanMessage)

    def test_summarize_messages_delegates_to_graph_worker(self):
        older = [
            HumanMessage(content="First request"),
            AIMessage(content="First response")
        ]
        res = self.pruner._summarize_messages(older, previous_summary="")
        self.assertEqual(res, "Mock summarized history")
        self.mock_worker.assert_called_once()

    def test_summarize_messages_fallback_to_heuristic_when_worker_fails(self):
        self.mock_worker.return_value = ""

        older = [
            HumanMessage(content="User requested code refactoring for module X."),
            AIMessage(content="Refactored module X successfully.")
        ]
        res = self.pruner._summarize_messages(older, previous_summary="")
        # Should fallback to heuristic summary without crashing
        self.assertIn("module X", res)

    def test_prune_messages_under_threshold_remains_untouched(self):
        messages = [
            HumanMessage(content="Short query"),
            AIMessage(content="Short response")
        ]
        Config().context_max_tokens = 30000
        Config().context_window_messages = 15
        result = self.pruner.prune_messages(messages)
        self.assertEqual(result, messages)

    def test_prune_messages_exceeding_token_threshold(self):
        # Create history exceeding 30k tokens (~120k characters)
        messages = []
        for i in range(30):
            messages.append(HumanMessage(content=f"Query {i}: " + ("data " * 500)))
            messages.append(AIMessage(content=f"Response {i}: " + ("analysis " * 500)))

        self.assertGreater(estimate_total_tokens(messages), 30000)

        Config().context_max_tokens = 30000
        Config().context_window_messages = 10
        pruned = self.pruner.prune_messages(messages)

        # First message should be the summary SystemMessage
        self.assertIsInstance(pruned[0], SystemMessage)
        self.assertIn(SUMMARY_PREFIX, pruned[0].content)

        # Pruned length should be smaller than original
        self.assertLess(len(pruned), len(messages))
        # Total tokens should be drastically reduced
        self.assertLess(estimate_total_tokens(pruned), estimate_total_tokens(messages))

    def test_prune_messages_force_flag(self):
        messages = [
            HumanMessage(content=f"Query {i}")
            for i in range(12)
        ]
        Config().context_window_messages = 4
        pruned = self.pruner.prune_messages(messages, force=True)
        self.assertIsInstance(pruned[0], SystemMessage)
        self.assertIn(SUMMARY_PREFIX, pruned[0].content)
        self.assertEqual(len(pruned), 5)  # 1 summary + 4 recent

    def test_prune_messages_respects_disabled_config(self):
        Config().context_pruning_enabled = False
        messages = [HumanMessage(content="Large text " * 10000) for _ in range(10)]
        result = self.pruner.prune_messages(messages)
        self.assertEqual(len(result), len(messages))

    def test_summarize_messages_via_graph_worker(self):
        self.mock_worker.return_value = "Graph worker generated summary of turns."

        older = [
            HumanMessage(content="First request"),
            AIMessage(content="First response")
        ]
        res = self.pruner._summarize_messages(older, previous_summary="")
        self.assertEqual(res, "Graph worker generated summary of turns.")
        self.mock_worker.assert_called_once()

    def test_prune_messages_guarantees_human_message_at_start_of_recent(self):
        messages = [
            HumanMessage(content="Initial query"),
            AIMessage(content="", tool_calls=[{"name": "t0", "args": {}, "id": "c0"}]),
            ToolMessage(content="output 0", tool_call_id="c0", name="t0"),
            AIMessage(content="Analysis 0"),
            HumanMessage(content="Followup query"),
            AIMessage(content="", tool_calls=[{"name": "t1", "args": {}, "id": "c1"}]),
            ToolMessage(content="output 1", tool_call_id="c1", name="t1"),
            AIMessage(content="", tool_calls=[{"name": "t2", "args": {}, "id": "c2"}]),
            ToolMessage(content="output 2", tool_call_id="c2", name="t2"),
            AIMessage(content="Analysis 1"),
            HumanMessage(content="Latest query"),
        ]
        Config().context_window_messages = 5
        pruned = self.pruner.prune_messages(messages, force=True)
        # First message is summary SystemMessage
        self.assertIsInstance(pruned[0], SystemMessage)
        # First dialogue message after SystemMessage MUST be HumanMessage
        self.assertIsInstance(pruned[1], HumanMessage)
        self.assertNotIsInstance(pruned[1], (AIMessage, ToolMessage))


    @patch('core.knowledge.memory.sqlite_checkpointer.SqliteCheckpointer.get_tuple')
    @patch('core.knowledge.memory.sqlite_checkpointer.SqliteCheckpointer.put')
    def test_auto_prune_session_prunes_and_updates_checkpoint(self, mock_put, mock_get_tuple):
        session = SessionManager.get_session(agent_id="test", source="discord", channel="general")
        mock_tuple = MagicMock()
        mock_tuple.config = {"configurable": {"thread_id": session.session_id}}
        mock_tuple.metadata = {}
        mock_tuple.checkpoint = {
            "channel_values": {
                "messages": [HumanMessage(content=f"Query {i}: " + ("data " * 300)) for i in range(25)]
            },
            "versions_seen": {}
        }
        mock_get_tuple.return_value = mock_tuple

        Config().context_max_tokens = 1000
        Config().context_window_messages = 5
        res = self.pruner.auto_prune_session(session)
        self.assertTrue(res)
        mock_put.assert_called_once()
        saved_messages = mock_tuple.checkpoint["channel_values"]["messages"]
        self.assertIsInstance(saved_messages[0], SystemMessage)
        self.assertIn(SUMMARY_PREFIX, saved_messages[0].content)

    @patch('core.knowledge.memory.sqlite_checkpointer.SqliteCheckpointer.get_tuple')
    @patch('core.knowledge.memory.sqlite_checkpointer.SqliteCheckpointer.put')
    def test_auto_prune_session_skips_when_under_threshold(self, mock_put, mock_get_tuple):
        session = SessionManager.get_session(agent_id="test", source="discord", channel="general")
        mock_tuple = MagicMock()
        mock_tuple.checkpoint = {
            "channel_values": {
                "messages": [HumanMessage(content="Short message")]
            }
        }
        mock_get_tuple.return_value = mock_tuple

        Config().context_max_tokens = 30000
        Config().context_window_messages = 15
        res = self.pruner.auto_prune_session(session)
        self.assertFalse(res)
        mock_put.assert_not_called()

    @patch('core.knowledge.memory.sqlite_checkpointer.SqliteCheckpointer.get_tuple')
    @patch('core.knowledge.memory.sqlite_checkpointer.SqliteCheckpointer.put')
    async def test_aauto_prune_session_prunes_checkpoint(self, mock_put, mock_get_tuple):
        session = SessionManager.get_session(agent_id="test", source="discord", channel="general")
        mock_tuple = MagicMock()
        mock_tuple.config = {"configurable": {"thread_id": session.session_id}}
        mock_tuple.metadata = {}
        mock_tuple.checkpoint = {
            "channel_values": {
                "messages": [HumanMessage(content=f"Query {i}: " + ("data " * 300)) for i in range(25)]
            },
            "versions_seen": {}
        }
        mock_get_tuple.return_value = mock_tuple

        Config().context_max_tokens = 1000
        Config().context_window_messages = 5
        res = await self.pruner.aauto_prune_session(session)
        self.assertTrue(res)
        mock_put.assert_called_once()
        saved_messages = mock_tuple.checkpoint["channel_values"]["messages"]
        self.assertIsInstance(saved_messages[0], SystemMessage)
        self.assertIn(SUMMARY_PREFIX, saved_messages[0].content)

    @patch('core.knowledge.memory.sqlite_checkpointer.SqliteCheckpointer.get_tuple')
    @patch('core.knowledge.memory.sqlite_checkpointer.SqliteCheckpointer.put')
    async def test_aauto_prune_session_summarizes_via_async_worker_not_sync_bridge(self, mock_put, mock_get_tuple):
        """The async prune path must stay async all the way down.

        aauto_prune_session -> aprune_messages -> _asummarize_messages ->
        _asummarize_with_graph_worker. If any link reverts to the synchronous
        _summarize_with_graph_worker, that bridge spins up a ThreadPoolExecutor and
        asyncio.run()s a second event loop from inside the running one
        (context_pruner.py:177-188) — the exact hazard the async pair exists to avoid.
        This test pins the summary text to the async worker and asserts the sync
        bridge is never touched.
        """
        session = SessionManager.get_session(agent_id="test", source="discord", channel="general")
        mock_tuple = MagicMock()
        mock_tuple.config = {"configurable": {"thread_id": session.session_id}}
        mock_tuple.metadata = {}
        mock_tuple.checkpoint = {
            "channel_values": {
                "messages": [HumanMessage(content=f"Query {i}: " + ("data " * 300)) for i in range(25)]
            },
            "versions_seen": {}
        }
        mock_get_tuple.return_value = mock_tuple

        Config().context_max_tokens = 1000
        Config().context_window_messages = 5

        with patch.object(
            ContextPruner,
            '_asummarize_with_graph_worker',
            new=AsyncMock(return_value="Async worker summary of the older turns")
        ) as mock_async_worker:
            res = await self.pruner.aauto_prune_session(session)

        self.assertTrue(res)
        mock_async_worker.assert_awaited_once()
        # setUp patches the synchronous bridge; it must not have been used.
        self.mock_worker.assert_not_called()

        saved_messages = mock_tuple.checkpoint["channel_values"]["messages"]
        self.assertIsInstance(saved_messages[0], SystemMessage)
        self.assertIn("Async worker summary of the older turns", saved_messages[0].content)
        # Only the summary plus the configured recent window survives.
        self.assertEqual(len(saved_messages), 6)
        self.assertTrue(all(isinstance(m, HumanMessage) for m in saved_messages[1:]))

    async def test_summarize_with_graph_worker_timeout_fallback(self):
        # Stop default mock to test actual _summarize_with_graph_worker timeout handling
        self.worker_patcher.stop()

        import asyncio

        async def slow_agent_call(*args, **kwargs):
            await asyncio.sleep(0.5)
            return "<summary>Delayed summary</summary>"

        mock_tool = MagicMock()
        mock_tool.ainvoke = AsyncMock(side_effect=slow_agent_call)

        with patch('tools.agent_call.agent_call', mock_tool):
            Config().context_pruning_timeout = 0.05
            pruner = ContextPruner()
            res = pruner._summarize_with_graph_worker(transcript="User: Hello")
            self.assertEqual(res, "")

            # Verify that summarize_messages falls back to heuristic summary without error
            fallback_res = pruner._summarize_messages([HumanMessage(content="Hello world")])
            self.assertIn("Hello world", fallback_res)

        # Restart mock for teardown
        self.mock_worker = self.worker_patcher.start()

    async def test_asummarize_with_graph_worker_timeout_fallback(self):
        import asyncio

        async def slow_agent_call(*args, **kwargs):
            await asyncio.sleep(0.5)
            return "<summary>Delayed summary</summary>"

        mock_tool = MagicMock()
        mock_tool.ainvoke = AsyncMock(side_effect=slow_agent_call)

        with patch('tools.agent_call.agent_call', mock_tool):
            Config().context_pruning_timeout = 0.05
            pruner = ContextPruner()
            res = await pruner._asummarize_with_graph_worker(transcript="User: Hello")
            self.assertEqual(res, "")

            fallback_res = await pruner._asummarize_messages([HumanMessage(content="Async hello world")])
            self.assertIn("Async hello world", fallback_res)

    async def test_summarize_with_graph_worker_error_payload_fallback(self):
        self.worker_patcher.stop()

        error_xml = '<tool_response name="agent_call"><payload></payload><errors>Error: Rate limit exceeded</errors></tool_response>'
        mock_tool = MagicMock()
        mock_tool.ainvoke = AsyncMock(return_value=error_xml)

        with patch('tools.agent_call.agent_call', mock_tool):
            pruner = ContextPruner()
            res = await pruner._asummarize_with_graph_worker(transcript="User: Test")
            self.assertEqual(res, "")

            fallback_res = await pruner._asummarize_messages([HumanMessage(content="Test error visibility")])
            self.assertIn("Test error visibility", fallback_res)

        self.mock_worker = self.worker_patcher.start()

    @patch('core.knowledge.memory.sqlite_checkpointer.SqliteCheckpointer.get_tuple', side_effect=Exception("Database lock error"))
    async def test_aauto_prune_session_handles_checkpointer_exception(self, mock_get_tuple):
        session = SessionManager.get_session(agent_id="test", source="discord", channel="general")
        Config().context_max_tokens = 1000
        pruner = ContextPruner()
        res = await pruner.aauto_prune_session(session)
        self.assertFalse(res)

    def test_config_context_pruning_timeout(self):
        cfg = Config()
        self.assertEqual(cfg.context_pruning_timeout, 30)

        cfg.context_pruning_timeout = 45
        self.assertEqual(cfg.context_pruning_timeout, 45)

        with patch.dict(os.environ, {"CONTEXT_PRUNING_TIMEOUT": "60"}):
            cfg.load_from_env()
            self.assertEqual(cfg.context_pruning_timeout, 60)


if __name__ == "__main__":
    unittest.main()
