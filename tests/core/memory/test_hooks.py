import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

import core.memory.session_message_hook as hooks

class TestHooks(unittest.IsolatedAsyncioTestCase):

    @patch('core.memory.session_message_hook.manager')
    async def test_hook_pre_message(self, mock_manager):
        await hooks.hook_pre_message("session1", "hello")
        mock_manager.append_message.assert_called_once_with("session1", "human", "hello")

    @patch('core.memory.session_message_hook.manager')
    async def test_hook_pre_message_new(self, mock_manager):
        # Now [new] is just a normal message for the hook, logged as usual.
        # Command handling is moved to Agent.process_message.
        await hooks.hook_pre_message("session1", "[new]")
        mock_manager.append_message.assert_called_once_with("session1", "human", "[new]")

    @patch('core.memory.session_message_hook.manager')
    async def test_hook_post_message(self, mock_manager):
        await hooks.hook_post_message("session1", "Reply text")
        mock_manager.append_message.assert_called_once_with("session1", "agent", "Reply text")

if __name__ == "__main__":
    unittest.main()
