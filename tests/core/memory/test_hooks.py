import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

import core.memory.session_message_hook as hooks

class TestHooks(unittest.IsolatedAsyncioTestCase):

    @patch('core.util.get_session_id')
    @patch('core.memory.session_message_hook.manager')
    async def test_hook_pre_message_new(self, mock_manager, mock_get_session_id):
        mock_get_session_id.return_value = "session1"
        mock_manager.archive_session.return_value = "Archived"
        
        mock_message = MagicMock()
        mock_message.content = "[new]"
        mock_message.channel.send = AsyncMock()
        
        mock_bot = MagicMock()

        result = await hooks.hook_pre_message(mock_message, mock_bot)

        self.assertIsNone(result)
        mock_manager.archive_session.assert_called_once_with("session1")
        mock_message.channel.send.assert_called_once_with("Session context cleared. Archived")

    @patch('core.util.get_session_id')
    async def test_hook_pre_message_other(self, mock_get_session_id):
        mock_get_session_id.return_value = "session1"
        
        mock_message = MagicMock()
        mock_message.content = "hello"
        
        mock_bot = MagicMock()

        # Fall through commented code
        result = await hooks.hook_pre_message(mock_message, mock_bot)
        self.assertIsNone(result)

    @patch('core.util.get_session_id')
    @patch('core.memory.session_message_hook.manager')
    async def test_hook_post_message(self, mock_manager, mock_get_session_id):
        mock_get_session_id.return_value = "session1"
        
        mock_message = MagicMock()
        mock_bot = MagicMock()
        
        await hooks.hook_post_message(mock_message, mock_bot, "Reply text")

        mock_manager.append_message.assert_called_once_with("session1", "agent", "Reply text")

if __name__ == "__main__":
    unittest.main()
