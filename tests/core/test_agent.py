import unittest
import os
import sys
from unittest.mock import patch, MagicMock, AsyncMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.agent import Agent

class TestAgent(unittest.IsolatedAsyncioTestCase):

    @patch('core.agent.HookLoader')
    @patch('core.debug_handler.DebugLogHandler')
    @patch('core.util.get_session_id')
    async def test_process_message_success(self, mock_get_session_id, mock_debug_handler_class, mock_hook_loader_class):
        # Setup mocks
        mock_hook_loader = MagicMock()
        mock_pre_hook = AsyncMock()
        mock_post_hook = AsyncMock()
        mock_hook_loader.pre_message_hooks = [mock_pre_hook]
        mock_hook_loader.post_message_hooks = [mock_post_hook]
        mock_hook_loader_class.return_value = mock_hook_loader
        
        mock_get_session_id.return_value = "session1"
        mock_debug_handler = MagicMock()
        mock_debug_handler_class.return_value = mock_debug_handler

        mock_message = MagicMock()
        mock_message.channel.send = AsyncMock()
        mock_bot = MagicMock()
        
        # Graph invoke result
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="Reply text")]})

        agent = Agent(mock_graph)
        
        # Run
        await agent.process_message(mock_message, mock_bot)
        
        # Assertions
        mock_pre_hook.assert_called_once_with(mock_message, mock_bot)
        mock_graph.ainvoke.assert_called_once()
        mock_post_hook.assert_called_once_with(mock_message, mock_bot, "Reply text")
        mock_message.channel.send.assert_called_once_with("Reply text")

if __name__ == "__main__":
    unittest.main()
