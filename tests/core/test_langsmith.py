import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.agent.agent import Agent
from core.agent.script_executor_agent import ScriptExecutorAgent
from tools.graph_call import graph_call
from scripts.verify_langsmith import verify_langsmith


class TestLangSmithIntegration(unittest.IsolatedAsyncioTestCase):

    @patch('core.agent.agent.LoggingHandler')
    async def test_agent_passes_tracing_metadata_and_tags(self, mock_logging_handler_class):
        mock_logging_handler = MagicMock()
        mock_logging_handler_class.return_value = mock_logging_handler

        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="Reply")]})

        agent = Agent("software-coder", {})
        agent.graph = mock_graph

        await agent.execute("Fix the bug", source="discord", role="user")

        mock_graph.ainvoke.assert_called_once()
        args, kwargs = mock_graph.ainvoke.call_args
        config = kwargs.get("config", {})

        # Verify LangSmith tracing tags, run_name, and metadata
        self.assertEqual(config.get("run_name"), "agent:software-coder")
        self.assertIn("software-coder", config.get("tags", []))
        self.assertIn("discord", config.get("tags", []))
        self.assertIn("role:user", config.get("tags", []))

        metadata = config.get("metadata", {})
        self.assertEqual(metadata.get("agent_id"), "software-coder")
        self.assertEqual(metadata.get("source"), "discord")
        self.assertEqual(metadata.get("role"), "user")

    @patch('tools.graph_call.GraphsLoader')
    async def test_graph_call_passes_tracing_config(self, mock_graphs_loader_class):
        mock_loader = MagicMock()
        mock_graphs_loader_class.return_value = mock_loader

        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="Subgraph Done")]})

        mock_loader.get_graph.return_value = {
            "graph": mock_graph,
            "metadata": {"name": "coding"}
        }

        result = await graph_call.ainvoke({"graph_name": "coding", "query": "Build feature"})

        mock_graph.ainvoke.assert_called_once()
        args, kwargs = mock_graph.ainvoke.call_args
        config = kwargs.get("config", {})

        self.assertEqual(config.get("run_name"), "graph:coding")
        self.assertEqual(config.get("tags"), ["graph", "coding"])
        self.assertEqual(config.get("metadata"), {"graph_name": "coding"})
        self.assertIn("Subgraph Done", result)

    @patch.dict(os.environ, {"LANGSMITH_TRACING": "false"}, clear=False)
    def test_verify_script_when_disabled(self):
        # Should return True without error when disabled
        result = verify_langsmith()
        self.assertTrue(result)

    @patch.dict(os.environ, {"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": ""}, clear=False)
    def test_verify_script_when_key_missing(self):
        # Should return False when tracing is true but key is missing
        result = verify_langsmith()
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
