import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.build_subgraph import build_subgraph
from core.util import format_tool_response
from core.agent.job_manager import current_agent_id

class TestBuildSubgraphTool(unittest.IsolatedAsyncioTestCase):

    async def test_build_subgraph_missing_args(self):
        result = await build_subgraph.ainvoke({"subgraph_name": "", "query": ""})
        self.assertIn("requires 'subgraph_name' and 'query'", result)

    @patch('tools.build_subgraph.SubgraphsLoader')
    async def test_build_subgraph_not_found(self, mock_subgraphs_loader_class):
        mock_loader = MagicMock()
        mock_subgraphs_loader_class.return_value = mock_loader
        mock_loader.get_subgraph.return_value = None

        result = await build_subgraph.ainvoke({"subgraph_name": "unknown", "query": "do something"})
        self.assertIn("Subgraph 'unknown' not found", result)

    @patch('tools.build_subgraph.SubgraphsLoader')
    async def test_build_subgraph_without_caller(self, mock_subgraphs_loader_class):
        mock_loader = MagicMock()
        mock_subgraphs_loader_class.return_value = mock_loader

        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="Finished execution")]})

        mock_loader.get_subgraph.return_value = {
            "graph": mock_graph,
            "metadata": {"name": "coding"}
        }

        result = await build_subgraph.ainvoke({"subgraph_name": "coding", "query": "Build feature"})

        mock_graph.ainvoke.assert_called_once()
        args, kwargs = mock_graph.ainvoke.call_args
        inputs = args[0]
        self.assertEqual(inputs["query"], "Build feature")
        self.assertEqual(inputs["messages"][0]["content"], "Build feature")

        config = kwargs.get("config", {})
        self.assertEqual(config.get("run_name"), "subgraph:coding")
        self.assertEqual(config.get("tags"), ["subgraph", "coding"])
        self.assertEqual(config.get("metadata"), {"subgraph_name": "coding"})

        self.assertEqual(result, format_tool_response("build_subgraph", payload="Finished execution", errors="None"))

    @patch('tools.build_subgraph.SubgraphsLoader')
    async def test_build_subgraph_with_caller_param(self, mock_subgraphs_loader_class):
        mock_loader = MagicMock()
        mock_subgraphs_loader_class.return_value = mock_loader

        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="Finished execution")]})

        mock_loader.get_subgraph.return_value = {
            "graph": mock_graph,
            "metadata": {"name": "coding"}
        }

        result = await build_subgraph.ainvoke({
            "subgraph_name": "coding",
            "query": "Build feature",
            "caller": "main"
        })

        mock_graph.ainvoke.assert_called_once()
        args, kwargs = mock_graph.ainvoke.call_args
        inputs = args[0]
        expected_query = "<caller>main</caller>\nBuild feature"
        self.assertEqual(inputs["query"], expected_query)
        self.assertEqual(inputs["messages"][0]["content"], expected_query)

        config = kwargs.get("config", {})
        self.assertEqual(config.get("run_name"), "subgraph:coding")
        self.assertIn("caller:main", config.get("tags", []))
        self.assertEqual(config.get("metadata", {}).get("caller"), "main")
        self.assertEqual(config.get("metadata", {}).get("triggering_agent"), "main")

        self.assertEqual(result, format_tool_response("build_subgraph", payload="Finished execution", errors="None"))

    @patch('tools.build_subgraph.SubgraphsLoader')
    async def test_build_subgraph_with_contextvar_caller(self, mock_subgraphs_loader_class):
        from core.agent.job_manager import current_agent_id

        mock_loader = MagicMock()
        mock_subgraphs_loader_class.return_value = mock_loader

        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="Finished execution")]})

        mock_loader.get_subgraph.return_value = {
            "graph": mock_graph,
            "metadata": {"name": "coding"}
        }

        token = current_agent_id.set("topic-researcher")
        try:
            result = await build_subgraph.ainvoke({
                "subgraph_name": "coding",
                "query": "Research codebase"
            })

            mock_graph.ainvoke.assert_called_once()
            args, kwargs = mock_graph.ainvoke.call_args
            inputs = args[0]
            expected_query = "<caller>topic-researcher</caller>\nResearch codebase"
            self.assertEqual(inputs["query"], expected_query)
            self.assertEqual(inputs["messages"][0]["content"], expected_query)

            config = kwargs.get("config", {})
            self.assertIn("caller:topic-researcher", config.get("tags", []))
            self.assertEqual(config.get("metadata", {}).get("caller"), "topic-researcher")
            self.assertEqual(config.get("metadata", {}).get("triggering_agent"), "topic-researcher")
        finally:
            current_agent_id.reset(token)

    @patch('tools.build_subgraph.SubgraphsLoader')
    async def test_build_subgraph_does_not_duplicate_caller_tag(self, mock_subgraphs_loader_class):
        mock_loader = MagicMock()
        mock_subgraphs_loader_class.return_value = mock_loader

        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="Finished execution")]})

        mock_loader.get_subgraph.return_value = {
            "graph": mock_graph,
            "metadata": {"name": "coding"}
        }

        result = await build_subgraph.ainvoke({
            "subgraph_name": "coding",
            "query": "<caller>existing_agent</caller>\nBuild feature",
            "caller": "main"
        })

        mock_graph.ainvoke.assert_called_once()
        args, kwargs = mock_graph.ainvoke.call_args
        inputs = args[0]
        self.assertEqual(inputs["query"], "<caller>existing_agent</caller>\nBuild feature")
        self.assertEqual(inputs["messages"][0]["content"], "<caller>existing_agent</caller>\nBuild feature")


if __name__ == "__main__":
    unittest.main()
