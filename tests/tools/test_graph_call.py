import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.graph_call import graph_call
from core.util import format_tool_response

class TestGraphCallTool(unittest.IsolatedAsyncioTestCase):

    async def test_graph_call_missing_args(self):
        result = await graph_call.ainvoke({"graph_name": "", "query": ""})
        self.assertIn("requires 'graph_name' and 'query'", result)

    @patch('tools.graph_call.GraphsLoader')
    async def test_graph_call_not_found(self, mock_graphs_loader_class):
        mock_loader = MagicMock()
        mock_graphs_loader_class.return_value = mock_loader
        mock_loader.get_graph.return_value = None

        result = await graph_call.ainvoke({"graph_name": "unknown", "query": "do something"})
        self.assertIn("Graph 'unknown' not found", result)

    @patch('tools.graph_call.GraphsLoader')
    async def test_graph_call_without_caller(self, mock_graphs_loader_class):
        mock_loader = MagicMock()
        mock_graphs_loader_class.return_value = mock_loader

        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="Finished execution")]})

        mock_loader.get_graph.return_value = {
            "graph": mock_graph,
            "metadata": {"name": "coding"}
        }

        result = await graph_call.ainvoke({"graph_name": "coding", "query": "Build feature"})

        mock_graph.ainvoke.assert_called_once()
        args, kwargs = mock_graph.ainvoke.call_args
        inputs = args[0]
        self.assertEqual(inputs["query"], "Build feature")
        self.assertEqual(inputs["messages"][0]["content"], "Build feature")

        config = kwargs.get("config", {})
        self.assertEqual(config.get("run_name"), "graph:coding")
        self.assertEqual(config.get("tags"), ["graph", "coding"])
        self.assertEqual(config.get("metadata", {}).get("graph_name"), "coding")
        self.assertEqual(config.get("configurable", {}).get("thread_id"), "coding:default")

        self.assertEqual(result, format_tool_response("graph_call", payload="Finished execution", errors="None"))

    @patch('tools.graph_call.GraphsLoader')
    async def test_graph_call_with_caller_param(self, mock_graphs_loader_class):
        mock_loader = MagicMock()
        mock_graphs_loader_class.return_value = mock_loader

        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="Finished execution")]})

        mock_loader.get_graph.return_value = {
            "graph": mock_graph,
            "metadata": {"name": "coding"}
        }

        result = await graph_call.ainvoke({
            "graph_name": "coding",
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
        self.assertEqual(config.get("run_name"), "graph:coding")
        self.assertIn("caller:main", config.get("tags", []))
        self.assertEqual(config.get("metadata", {}).get("caller"), "main")
        self.assertEqual(config.get("metadata", {}).get("triggering_agent"), "main")

        self.assertEqual(result, format_tool_response("graph_call", payload="Finished execution", errors="None"))

    @patch('tools.graph_call.GraphsLoader')
    async def test_graph_call_with_contextvar_caller(self, mock_graphs_loader_class):
        from core.agent.job_manager import current_session_identifier
        from core.agent.session_identifier import SessionIdentifier

        mock_loader = MagicMock()
        mock_graphs_loader_class.return_value = mock_loader

        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="Finished execution")]})

        mock_loader.get_graph.return_value = {
            "graph": mock_graph,
            "metadata": {"name": "coding"}
        }

        from core.agent.session_manager import SessionManager
        sess = SessionManager.get_session(agent_id="topic-researcher", source="discord", channel="general")
        token = current_session_identifier.set(sess)
        try:
            result = await graph_call.ainvoke({
                "graph_name": "coding",
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
            current_session_identifier.reset(token)

    @patch('tools.graph_call.GraphsLoader')
    async def test_graph_call_does_not_duplicate_caller_tag(self, mock_graphs_loader_class):
        mock_loader = MagicMock()
        mock_graphs_loader_class.return_value = mock_loader

        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="Finished execution")]})

        mock_loader.get_graph.return_value = {
            "graph": mock_graph,
            "metadata": {"name": "coding"}
        }

        result = await graph_call.ainvoke({
            "graph_name": "coding",
            "query": "<caller>existing_agent</caller>\nBuild feature",
            "caller": "main"
        })

        mock_graph.ainvoke.assert_called_once()
        args, kwargs = mock_graph.ainvoke.call_args
        inputs = args[0]
        self.assertEqual(inputs["query"], "<caller>existing_agent</caller>\nBuild feature")
        self.assertEqual(inputs["messages"][0]["content"], "<caller>existing_agent</caller>\nBuild feature")

    @patch('tools.graph_call.GraphsLoader')
    async def test_graph_call_uses_graph_adapters(self, mock_graphs_loader_class):
        mock_loader = MagicMock()
        mock_graphs_loader_class.return_value = mock_loader

        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"custom_result": "done"})

        mock_prepare = MagicMock(return_value={"custom_input": "prepared"})
        mock_format = MagicMock(return_value="Custom Formatted Output")

        mock_loader.get_graph.return_value = {
            "graph": mock_graph,
            "prepare_input": mock_prepare,
            "format_output": mock_format,
            "metadata": {"name": "custom"}
        }

        result = await graph_call.ainvoke({"graph_name": "custom", "query": "Run custom flow"})

        mock_prepare.assert_called_once()
        mock_graph.ainvoke.assert_called_once()
        self.assertEqual(mock_graph.ainvoke.call_args[0][0], {"custom_input": "prepared"})
        mock_format.assert_called_once_with({"custom_result": "done"})
        self.assertEqual(result, format_tool_response("graph_call", payload="Custom Formatted Output", errors="None"))

    @patch('tools.graph_call.GraphsLoader')
    async def test_graph_call_resumes_interrupted_thread(self, mock_graphs_loader_class):
        mock_loader = MagicMock()
        mock_graphs_loader_class.return_value = mock_loader

        mock_graph = MagicMock()
        state_snap = MagicMock()
        state_snap.next = ("hitl_image_and_plot_approval",)
        mock_graph.get_state = MagicMock(return_value=state_snap)
        mock_graph.aupdate_state = AsyncMock()
        mock_graph.ainvoke = AsyncMock(return_value={"resumed": True})

        mock_loader.get_graph.return_value = {
            "graph": mock_graph,
            "metadata": {"name": "content_creation"}
        }

        result = await graph_call.ainvoke({"graph_name": "content_creation", "query": "approved"})

        mock_graph.get_state.assert_called()
        mock_graph.aupdate_state.assert_called_once()
        self.assertEqual(mock_graph.aupdate_state.call_args[0][1]["latest_human_feedback"], "approved")
        mock_graph.ainvoke.assert_called_once_with(None, config=mock_graph.aupdate_state.call_args[0][0])

    @patch('tools.graph_call.GraphsLoader')
    async def test_graph_call_resumes_interrupted_channel_thread(self, mock_graphs_loader_class):
        from core.agent.job_manager import current_session_identifier
        from core.agent.session_manager import SessionManager
        sess = SessionManager.get_session(agent_id="main", source="discord", channel="content-creation")
        token = current_session_identifier.set(sess)

        try:
            mock_loader = MagicMock()
            mock_graphs_loader_class.return_value = mock_loader

            mock_graph = MagicMock()
            state_snap = MagicMock()
            state_snap.next = ("hitl_image_and_plot_approval",)
            mock_graph.get_state = MagicMock(return_value=state_snap)
            mock_graph.aupdate_state = AsyncMock()
            mock_graph.ainvoke = AsyncMock(return_value={"resumed": True})

            mock_loader.get_graph.return_value = {
                "graph": mock_graph,
                "metadata": {"name": "content_creation"}
            }

            feedback = "i am looking for ayla in a full fish mascot outfit, instead of wearing a jacket with fish icons."
            result = await graph_call.ainvoke({"graph_name": "content_creation", "query": feedback})

            mock_graph.aupdate_state.assert_called_once()
            called_payload = mock_graph.aupdate_state.call_args[0][1]
            self.assertEqual(called_payload["latest_human_feedback"], feedback)
            called_config = mock_graph.aupdate_state.call_args[0][0]
            self.assertEqual(called_config["configurable"]["thread_id"], sess.get_session_thread_id("content_creation"))
            mock_graph.ainvoke.assert_called_once_with(None, config=called_config)
        finally:
            current_session_identifier.reset(token)

    @patch('tools.graph_call.GraphsLoader')
    async def test_graph_call_sets_current_graph_id_context(self, mock_graphs_loader_class):
        from core.agent.job_manager import current_graph_id
        
        captured_graph_id = None
        async def mock_ainvoke(*args, **kwargs):
            nonlocal captured_graph_id
            captured_graph_id = current_graph_id.get()
            return {"messages": [MagicMock(content="Done")]}

        mock_loader = MagicMock()
        mock_graphs_loader_class.return_value = mock_loader

        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(side_effect=mock_ainvoke)

        mock_loader.get_graph.return_value = {
            "graph": mock_graph,
            "metadata": {"name": "coding", "graph_id": "coding"}
        }

    @patch('tools.graph_call.GraphsLoader')
    async def test_graph_call_with_thread_context(self, mock_graphs_loader_class):
        import discord
        from core.agent.job_manager import current_session_identifier
        from core.agent.session_manager import SessionManager

        mock_loader = MagicMock()
        mock_graphs_loader_class.return_value = mock_loader

        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="Finished in thread")]})

        mock_loader.get_graph.return_value = {
            "graph": mock_graph,
            "metadata": {"name": "coding"}
        }

        mock_thread = MagicMock(spec=discord.Thread)
        mock_thread.id = 1541110915540324533
        mock_thread.name = "sub-feature"
        mock_thread.parent = MagicMock(spec=discord.TextChannel)
        mock_thread.parent.name = "software-dev"

        sess = SessionManager.get_session(agent_id="main", source="discord", channel=mock_thread)
        tok = current_session_identifier.set(sess)
        try:
            result = await graph_call.ainvoke({"graph_name": "coding", "query": "Build feature"})

            mock_graph.ainvoke.assert_called_once()
            args, kwargs = mock_graph.ainvoke.call_args
            config = kwargs.get("config", {})
            self.assertEqual(
                config.get("configurable", {}).get("thread_id"),
                sess.get_session_thread_id("coding")
            )
            self.assertEqual(result, format_tool_response("graph_call", payload="Finished in thread", errors="None"))
        finally:
            current_session_identifier.reset(tok)


if __name__ == "__main__":
    unittest.main()
