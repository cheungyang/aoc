import unittest
import os
import sys
from unittest.mock import patch, MagicMock, AsyncMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.agent.agent import Agent

class TestAgent(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from core.agent.job_manager import JobManager
        JobManager._instance = None


    @patch('core.agent.agent.LoggingHandler')
    async def test_execute_success(self, mock_logging_handler_class):
        # Setup mocks
        mock_logging_handler = MagicMock()
        mock_logging_handler_class.return_value = mock_logging_handler

        # Graph invoke result
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="Reply text")]})

        agent = Agent("test-agent", {})
        agent.graph = mock_graph
        
        # Run
        reply = await agent.execute("hello", "session1")
        
        # Assertions
        mock_graph.ainvoke.assert_called_once()
        self.assertEqual(reply, "Reply text")

    

    @patch('core.agent.agent.LoggingHandler')
    async def test_execute_invoke_failure(self, mock_logging_handler_class):
        # Graph invoke throws exception
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(side_effect=Exception("Invoke failed"))

        agent = Agent("test-agent", {})
        agent.graph = mock_graph
        
        # Run
        reply = await agent.execute("hello", "session1")
        
        # Assertions
        self.assertEqual(reply, "Sorry, I encountered an error processing the request.")

    @patch('core.agent.agent.LoggingHandler')
    @patch('core.agent.session_manager.SessionManager.get_session_id')
    async def test_execute_parsing_failure(self, mock_get_session_id, mock_logging_handler_class):
        # Graph invoke succeeds but returns empty messages list (causing IndexError)
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": []})

        agent = Agent("test-agent", {})
        agent.graph = mock_graph
        
        # Run and Expect IndexError
        with self.assertRaises(IndexError):
            await agent.execute("hello", "session1")

    @patch('core.agent.agent.LoggingHandler')
    @patch('core.memory.flat_file_checkpointer.FlatFileCheckpointer.delete_thread')
    async def test_execute_retry_on_corrupt_checkpointer(self, mock_delete_thread, mock_logging_handler_class):
        # Graph invoke throws corrupt checkpointer exception on first call, succeeds on second
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock()
        mock_graph.ainvoke.side_effect = [
            Exception("Found AIMessages with tool_calls that do not have a corresponding ToolMessage"),
            {"messages": [MagicMock(content="Success after retry")]}
        ]

        agent = Agent("test-agent", {})
        agent.graph = mock_graph
        
        # Run
        reply = await agent.execute("hello", "session1")
        
        # Assertions
        self.assertEqual(mock_graph.ainvoke.call_count, 2)
        mock_delete_thread.assert_called_once_with("test-agent:session1")
        self.assertEqual(reply, "Success after retry")


if __name__ == "__main__":
    unittest.main()
