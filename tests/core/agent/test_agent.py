import unittest
import os
import sys
from unittest.mock import patch, MagicMock, AsyncMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.agent.agent import Agent

class TestAgent(unittest.IsolatedAsyncioTestCase):

    @patch('core.agent.agent.HookLoader')
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
        mock_pre_hook.assert_called_once_with("session1", mock_message.content)
        mock_graph.ainvoke.assert_called_once()
        mock_post_hook.assert_called_once_with("session1", "Reply text")
        mock_message.channel.send.assert_called_once_with("Reply text")

    @patch('core.agent.agent.HookLoader')
    @patch('core.debug_handler.DebugLogHandler')
    @patch('core.util.get_session_id')
    async def test_process_message_pre_hook_stop(self, mock_get_session_id, mock_debug_handler_class, mock_hook_loader_class):
        # Setup mocks
        mock_hook_loader = MagicMock()
        mock_pre_hook_called = False
        async def mock_pre_hook(session_id, content):
            nonlocal mock_pre_hook_called
            mock_pre_hook_called = True
            return "STOP"
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
        
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="Reply text")]})

        agent = Agent(mock_graph)
        
        await agent.process_message(mock_message, mock_bot)
        
        self.assertTrue(mock_pre_hook_called)
        mock_graph.ainvoke.assert_not_called()
        mock_post_hook.assert_not_called()
        mock_message.channel.send.assert_not_called()

    @patch('core.agent.agent.HookLoader')
    @patch('core.debug_handler.DebugLogHandler')
    @patch('core.util.get_session_id')
    async def test_process_message_invoke_failure(self, mock_get_session_id, mock_debug_handler_class, mock_hook_loader_class):
        # Setup mocks
        mock_hook_loader = MagicMock()
        mock_hook_loader.pre_message_hooks = []
        mock_hook_loader_class.return_value = mock_hook_loader
        
        mock_message = MagicMock()
        mock_message.channel.send = AsyncMock()
        mock_bot = MagicMock()
        
        # Graph invoke throws exception
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(side_effect=Exception("Invoke failed"))

        agent = Agent(mock_graph)
        
        # Run
        await agent.process_message(mock_message, mock_bot)
        
        # Assertions
        mock_message.channel.send.assert_called_once_with("Sorry, I encountered an error processing the request.")

    @patch('core.agent.agent.HookLoader')
    @patch('core.debug_handler.DebugLogHandler')
    @patch('core.util.get_session_id')
    async def test_process_message_parsing_failure(self, mock_get_session_id, mock_debug_handler_class, mock_hook_loader_class):
        # Setup mocks
        mock_hook_loader = MagicMock()
        mock_hook_loader.pre_message_hooks = []
        mock_hook_loader_class.return_value = mock_hook_loader
        
        mock_message = MagicMock()
        mock_message.channel.send = AsyncMock()
        mock_bot = MagicMock()
        
        # Graph invoke succeeds but returns empty messages list (causing IndexError)
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": []})

        agent = Agent(mock_graph)
        
        # Run and Expect IndexError (not caught by Agent's loop)
        with self.assertRaises(IndexError):
            await agent.process_message(mock_message, mock_bot)

if __name__ == "__main__":
    unittest.main()
