import unittest
import os
import sys
from unittest.mock import patch, MagicMock, AsyncMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.agent.command_handler import CommandHandler

class TestCommandHandler(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.handler = CommandHandler()

    async def test_non_string_content_returns_false(self):
        result = await self.handler.handle_command([{"type": "text", "text": "hello"}])
        self.assertFalse(result)

    async def test_unrecognized_command_returns_false(self):
        result = await self.handler.handle_command("regular user prompt")
        self.assertFalse(result)

    @patch('core.agent.session_manager.SessionManager.clear_session')
    async def test_handle_new_command_with_channel(self, mock_clear_session):
        mock_clear_session.return_value = "archived table"
        mock_channel = AsyncMock()

        result = await self.handler.handle_command("[new]", session_id="test-session", channel=mock_channel)
        self.assertTrue(result)
        mock_clear_session.assert_called_once_with("test-session")
        mock_channel.send.assert_called_once_with("Session context cleared. archived table")

    @patch('core.agent.session_manager.SessionManager.clear_session')
    async def test_handle_new_command_without_channel(self, mock_clear_session):
        mock_clear_session.return_value = "archived table"

        result = await self.handler.handle_command("[new]", session_id="test-session", channel=None)
        self.assertTrue(result)
        mock_clear_session.assert_called_once_with("test-session")

    @patch('core.agent.session_manager.SessionManager.clear_sessions')
    async def test_handle_newall_command_with_channel(self, mock_clear_sessions):
        mock_clear_sessions.return_value = "all archived"
        mock_channel = AsyncMock()

        result = await self.handler.handle_command("[newall]", channel=mock_channel)
        self.assertTrue(result)
        mock_clear_sessions.assert_called_once()
        mock_channel.send.assert_called_once_with("All session contexts cleared. all archived")

    @patch('core.agent.session_manager.SessionManager.clear_sessions')
    async def test_handle_newall_command_without_channel(self, mock_clear_sessions):
        mock_clear_sessions.return_value = "all archived"

        result = await self.handler.handle_command("[newall]", channel=None)
        self.assertTrue(result)
        mock_clear_sessions.assert_called_once()

    @patch('core.agent.command_handler.os.execv')
    async def test_handle_restart_command_with_channel(self, mock_execv):
        mock_channel = AsyncMock()

        result = await self.handler.handle_command("[restart]", channel=mock_channel)
        self.assertTrue(result)
        mock_channel.send.assert_called_once_with("System is restarting...")
        mock_execv.assert_called_once_with(sys.executable, [sys.executable] + sys.argv)

    @patch('core.agent.command_handler.os.execv')
    async def test_handle_restart_command_without_channel(self, mock_execv):
        result = await self.handler.handle_command("[restart]", channel=None)
        self.assertTrue(result)
        mock_execv.assert_called_once_with(sys.executable, [sys.executable] + sys.argv)

if __name__ == "__main__":
    unittest.main()
