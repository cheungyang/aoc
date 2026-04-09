import unittest
import os
import sys
from unittest.mock import patch, MagicMock, AsyncMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.agent.agent import Agent

class TestAgent(unittest.IsolatedAsyncioTestCase):

    @patch('core.agent.agent.HooksLoader')
    @patch('core.agent.debug_handler.DebugLogHandler')
    async def test_execute_success(self, mock_debug_handler_class, mock_hook_loader_class):
        # Setup mocks
        mock_hook_loader = MagicMock()
        mock_pre_hook = AsyncMock(return_value="hello")
        mock_post_hook = AsyncMock()
        mock_hook_loader.pre_message_hooks = [mock_pre_hook]
        mock_hook_loader.post_message_hooks = [mock_post_hook]
        mock_hook_loader_class.return_value = mock_hook_loader
        
        mock_debug_handler = MagicMock()
        mock_debug_handler_class.return_value = mock_debug_handler

        # Graph invoke result
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="Reply text")]})

        agent = Agent("test-agent", {})
        agent.graph = mock_graph
        
        # Run
        reply = await agent.execute("hello", "session1")
        
        # Assertions
        mock_pre_hook.assert_called_once_with("session1", "user", "hello")
        mock_graph.ainvoke.assert_called_once()
        mock_post_hook.assert_called_once_with("session1", "ai", "Reply text")
        self.assertEqual(reply, "Reply text")

    

    @patch('core.agent.agent.HooksLoader')
    @patch('core.agent.debug_handler.DebugLogHandler')
    async def test_execute_invoke_failure(self, mock_debug_handler_class, mock_hook_loader_class):
        # Setup mocks
        mock_hook_loader = MagicMock()
        mock_hook_loader.pre_message_hooks = []
        mock_hook_loader_class.return_value = mock_hook_loader
        
        # Graph invoke throws exception
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(side_effect=Exception("Invoke failed"))

        agent = Agent("test-agent", {})
        agent.graph = mock_graph
        
        # Run
        reply = await agent.execute("hello", "session1")
        
        # Assertions
        self.assertEqual(reply, "Sorry, I encountered an error processing the request.")

    @patch('core.agent.agent.HooksLoader')
    @patch('core.agent.debug_handler.DebugLogHandler')
    @patch('core.util.get_session_id')
    async def test_execute_parsing_failure(self, mock_get_session_id, mock_debug_handler_class, mock_hook_loader_class):
        # Setup mocks
        mock_hook_loader = MagicMock()
        mock_hook_loader.pre_message_hooks = []
        mock_hook_loader_class.return_value = mock_hook_loader
        
        # Graph invoke succeeds but returns empty messages list (causing IndexError)
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": []})

        agent = Agent("test-agent", {})
        agent.graph = mock_graph
        
        # Run and Expect IndexError
        with self.assertRaises(IndexError):
            await agent.execute("hello", "session1")




if __name__ == "__main__":
    unittest.main()
