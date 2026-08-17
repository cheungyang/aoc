import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.graph_status import graph_status
from core.util import format_tool_response
from core.agent.job_manager import current_channel_name, current_job_id, Job

class TestGraphStatusTool(unittest.TestCase):

    @patch('tools.graph_status.GraphsLoader')
    @patch('tools.graph_status.JobManager')
    def test_graph_status_no_active_graphs(self, mock_job_manager_class, mock_graphs_loader_class):
        mock_loader = MagicMock()
        mock_graphs_loader_class.return_value = mock_loader
        mock_loader.list_graph_names.return_value = ["content_creation", "coding"]
        
        mock_graph = MagicMock()
        mock_graph.get_state.return_value = None
        mock_loader.get_graph.return_value = {"graph": mock_graph}

        mock_jm = MagicMock()
        mock_job_manager_class.return_value = mock_jm
        mock_jm.get_jobs.return_value = []

        result = graph_status.func(channel="general")
        self.assertIn("No active or paused subgraphs found", result)
        self.assertIn("#general", result)

    @patch('tools.graph_status.GraphsLoader')
    @patch('tools.graph_status.JobManager')
    def test_graph_status_with_interrupted_subgraph(self, mock_job_manager_class, mock_graphs_loader_class):
        mock_loader = MagicMock()
        mock_graphs_loader_class.return_value = mock_loader
        mock_loader.list_graph_names.return_value = ["content_creation"]

        mock_snapshot = MagicMock()
        mock_snapshot.next = ("hitl_image_and_plot_approval",)
        mock_snapshot.values = {
            "topic": "fish",
            "project_dir": "pkm/wiki/software/ayla-first-words",
            "image_path": "images/fish.png",
            "video_plot_path": "plots/fish.md"
        }

        mock_graph = MagicMock()
        mock_graph.get_state.return_value = mock_snapshot
        mock_loader.get_graph.return_value = {"graph": mock_graph}

        mock_jm = MagicMock()
        mock_job_manager_class.return_value = mock_jm
        mock_jm.get_jobs.return_value = []

        result = graph_status.func(channel="content-creation")
        self.assertIn("Active Graph: content_creation", result)
        self.assertIn("hitl_image_and_plot_approval", result)
        self.assertIn("Topic: 'fish'", result)
        self.assertIn("Project: 'pkm/wiki/software/ayla-first-words'", result)
        self.assertIn("relay", result.lower())

    @patch('tools.graph_status.GraphsLoader')
    @patch('tools.graph_status.JobManager')
    def test_graph_status_with_specific_graph_name(self, mock_job_manager_class, mock_graphs_loader_class):
        mock_loader = MagicMock()
        mock_graphs_loader_class.return_value = mock_loader

        mock_snapshot = MagicMock()
        mock_snapshot.next = ("plan_node",)
        mock_snapshot.values = {"query": "fix bug in parser"}

        mock_graph = MagicMock()
        mock_graph.get_state.return_value = mock_snapshot
        mock_loader.get_graph.return_value = {"graph": mock_graph}

        mock_jm = MagicMock()
        mock_job_manager_class.return_value = mock_jm
        mock_jm.get_jobs.return_value = []

        result = graph_status.func(graph_name="coding", channel="software-dev")
        self.assertIn("Active Graph: coding", result)
        self.assertIn("plan_node", result)

    @patch('tools.graph_status.GraphsLoader')
    @patch('tools.graph_status.JobManager')
    def test_graph_status_with_contextvar_channel(self, mock_job_manager_class, mock_graphs_loader_class):
        mock_loader = MagicMock()
        mock_graphs_loader_class.return_value = mock_loader
        mock_loader.list_graph_names.return_value = ["content_creation"]

        mock_snapshot = MagicMock()
        mock_snapshot.next = ("hitl_final_package_approval",)
        mock_snapshot.values = {"topic": "cat", "gate2_decision": "pending"}

        mock_graph = MagicMock()
        mock_graph.get_state.return_value = mock_snapshot
        mock_loader.get_graph.return_value = {"graph": mock_graph}

        mock_jm = MagicMock()
        mock_job_manager_class.return_value = mock_jm
        mock_jm.get_jobs.return_value = []

        token = current_channel_name.set("content-creation")
        try:
            result = graph_status.func()
            self.assertIn("Active Graph: content_creation", result)
            self.assertIn("hitl_final_package_approval", result)
        finally:
            current_channel_name.reset(token)

    @patch('tools.graph_status.GraphsLoader')
    @patch('tools.graph_status.JobManager')
    def test_graph_status_with_running_background_jobs(self, mock_job_manager_class, mock_graphs_loader_class):
        mock_loader = MagicMock()
        mock_graphs_loader_class.return_value = mock_loader
        mock_loader.list_graph_names.return_value = []

        mock_jm = MagicMock()
        mock_job_manager_class.return_value = mock_jm
        job = Job(
            job_id="job_456",
            agent_id="content-creator",
            session_id="main:discord:content-creation",
            started=100.0,
            updated=105.0,
            status="running",
            initial_prompt="Generate video"
        )
        mock_jm.get_jobs.return_value = [job]

        result = graph_status.func(channel="content-creation")
        self.assertIn("Running Background Jobs", result)
        self.assertIn("job_456", result)
        self.assertIn("content-creator", result)

if __name__ == "__main__":
    unittest.main()
