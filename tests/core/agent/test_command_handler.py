import unittest
import os
import sys
from unittest.mock import patch, MagicMock, AsyncMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.agent.command_handler import CommandHandler
from core.agent.session_manager import SessionManager


class TestCommandHandler(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.handler = CommandHandler()

    async def test_non_string_content_returns_false(self):
        session = SessionManager.get_session(agent_id="test", source="discord", channel="general")
        result = await self.handler.handle_command([{"type": "text", "text": "hello"}], session=session)
        self.assertFalse(result)

    async def test_unrecognized_command_returns_false(self):
        session = SessionManager.get_session(agent_id="test", source="discord", channel="general")
        result = await self.handler.handle_command("regular user prompt", session=session)
        self.assertFalse(result)

    @patch('core.agent.session_manager.SessionManager.clear_session')
    async def test_handle_new_command_with_channel(self, mock_clear_session):
        mock_clear_session.return_value = "archived table"
        mock_channel = AsyncMock()
        mock_channel.name = "general"

        session = SessionManager.get_session(agent_id="test", source="discord", channel=mock_channel)
        result = await self.handler.handle_command("[new]", session=session)
        self.assertTrue(result)
        mock_clear_session.assert_called_once_with(session)
        mock_channel.send.assert_called_once_with("Session context cleared. archived table")

    @patch('core.agent.session_manager.SessionManager.clear_session')
    async def test_handle_new_command_without_channel(self, mock_clear_session):
        mock_clear_session.return_value = "archived table"

        session = SessionManager.get_session(agent_id="test", source="discord", channel="general")
        result = await self.handler.handle_command("[new]", session=session)
        self.assertTrue(result)
        mock_clear_session.assert_called_once_with(session)

    @patch('core.agent.session_manager.SessionManager.clear_sessions')
    async def test_handle_newall_command_with_channel(self, mock_clear_sessions):
        mock_clear_sessions.return_value = "all archived"
        mock_channel = AsyncMock()
        mock_channel.name = "general"

        session = SessionManager.get_session(agent_id="test", source="discord", channel=mock_channel)
        result = await self.handler.handle_command("[newall]", session=session)
        self.assertTrue(result)
        mock_clear_sessions.assert_called_once()
        mock_channel.send.assert_called_once_with("All session contexts cleared. all archived")

    @patch('core.agent.session_manager.SessionManager.clear_sessions')
    async def test_handle_newall_command_without_channel(self, mock_clear_sessions):
        mock_clear_sessions.return_value = "all archived"

        session = SessionManager.get_session(agent_id="test", source="discord", channel="general")
        result = await self.handler.handle_command("[newall]", session=session)
        self.assertTrue(result)
        mock_clear_sessions.assert_called_once()

    @patch('core.agent.command_handler.os.execv')
    async def test_handle_restart_command_with_channel(self, mock_execv):
        mock_channel = AsyncMock()
        mock_channel.name = "general"

        session = SessionManager.get_session(agent_id="test", source="discord", channel=mock_channel)
        result = await self.handler.handle_command("[restart]", session=session)
        self.assertTrue(result)
        mock_channel.send.assert_called_once_with("System is restarting...")
        mock_execv.assert_called_once_with(sys.executable, [sys.executable] + sys.argv)

    @patch('core.agent.command_handler.os.execv')
    async def test_handle_restart_command_without_channel(self, mock_execv):
        session = SessionManager.get_session(agent_id="test", source="discord", channel="general")
        result = await self.handler.handle_command("[restart]", session=session)
        self.assertTrue(result)
        mock_execv.assert_called_once_with(sys.executable, [sys.executable] + sys.argv)

    @patch('core.agent.context_pruner.ContextPruner._asummarize_with_graph_worker', return_value="Compacted summary")
    @patch('core.knowledge.memory.sqlite_checkpointer.SqliteCheckpointer.put')
    @patch('core.knowledge.memory.sqlite_checkpointer.SqliteCheckpointer.get_tuple')
    async def test_handle_compact_command(self, mock_get_tuple, mock_put, mock_worker):
        from langchain_core.messages import HumanMessage
        from core.util.config import Config
        Config().context_window_messages = 5
        mock_tuple = MagicMock()
        mock_tuple.config = {"configurable": {"thread_id": "test:discord:general"}}
        mock_tuple.metadata = {}
        mock_tuple.checkpoint = {
            "channel_values": {
                "messages": [
                    HumanMessage(content="A" * 500) for _ in range(20)
                ]
            }
        }
        mock_get_tuple.return_value = mock_tuple
        mock_channel = AsyncMock()
        mock_channel.name = "general"

        session = SessionManager.get_session(agent_id="test", source="discord", channel=mock_channel)
        result = await self.handler.handle_command("[compact]", session=session)
        self.assertTrue(result)
        mock_put.assert_called_once()
        mock_channel.send.assert_called()
        sent_text = mock_channel.send.call_args[0][0]
        self.assertIn("Session Context Compacted", sent_text)


if __name__ == "__main__":
    unittest.main()
