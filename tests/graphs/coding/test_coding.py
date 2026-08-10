import unittest
import os
import sys
import asyncio
import json
import shutil
from unittest.mock import patch, MagicMock, AsyncMock

# Add root directory to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.loaders.graphs_loader import GraphsLoader
from tools.graph_call import graph_call
from core.loaders.agents_loader import AgentsLoader

class TestCodingSubgraph(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.loader = AgentsLoader()
        
        # Backup original agents cache if any
        self.original_cache = dict(self.loader._agents_cache)
        
        # Create mock agents
        self.mock_planner = AsyncMock()
        self.mock_coder = AsyncMock()
        self.mock_qa = AsyncMock()
        
        self.loader._agents_cache["software-planner"] = self.mock_planner
        self.loader._agents_cache["software-coder"] = self.mock_coder
        self.loader._agents_cache["software-qa"] = self.mock_qa

        # Workspace path
        self.repo_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        self.session_file = os.path.join(self.repo_path, "sessions", "breakdown_test_session.json")

    def tearDown(self):
        # Restore original agents cache
        self.loader._agents_cache = self.original_cache
        
        # Clean up session file
        if os.path.exists(self.session_file):
            os.remove(self.session_file)

    @patch('graphs.coding.graph.git')
    async def test_coding_subgraph_success(self, mock_git):
        # 1. Setup mock responses
        # Planner returns a single task breakdown
        tasks_json = json.dumps([
            {
                "id": 1,
                "description": "Implement math functions",
                "file_path": "src/math_utils.py",
                "acceptance_criteria": "add(1, 2) returns 3"
            }
        ])
        self.mock_planner.execute = AsyncMock(return_value=tasks_json)
        self.mock_coder.execute = AsyncMock(return_value="Implemented add function.")
        self.mock_qa.execute = AsyncMock(return_value="VERDICT: PASS - Tests passed.")
        
        mock_git.invoke = MagicMock(return_value="Git response")

        # Load coding subgraph
        graphs_loader = GraphsLoader()
        subgraph_info = graphs_loader.get_graph("coding")
        self.assertIsNotNone(subgraph_info)
        
        graph = subgraph_info["graph"]
        
        # Run graph with inputs
        inputs = {
            "messages": [],
            "query": "Implement math functions",
            "session_id": "test_session",
            "repo_path": self.repo_path,
            "max_retries": 2,
            "max_concurrency": 1
        }
        
        result = await graph.ainvoke(inputs)
        
        # Assertions
        self.mock_planner.execute.assert_called_once()
        self.mock_coder.execute.assert_called_once()
        self.mock_qa.execute.assert_called_once()
        
        # Git add and commit should be invoked
        self.assertEqual(mock_git.invoke.call_count, 2)
        mock_git.invoke.assert_any_call({"command": "add .", "path": self.repo_path})
        mock_git.invoke.assert_any_call({"command": 'commit -m "feat: implement Implement math functions"', "path": self.repo_path})
        
        # Final message should be success message
        self.assertIn("Successfully implemented and verified all tasks", result["messages"][-1].content)
        self.assertEqual(len(result["completed_tasks"]), 1)
        self.assertEqual(len(result["failed_tasks"]), 0)

    @patch('graphs.coding.graph.git')
    async def test_coding_subgraph_retry_and_fail(self, mock_git):
        # Planner returns a single task
        tasks_json = json.dumps([
            {
                "id": 1,
                "description": "Implement database connection",
                "file_path": "src/db.py",
                "acceptance_criteria": "connect() succeeds"
            }
        ])
        self.mock_planner.execute = AsyncMock(return_value=tasks_json)
        self.mock_coder.execute = AsyncMock(return_value="Attempted connection.")
        # QA always fails
        self.mock_qa.execute = AsyncMock(return_value="VERDICT: FAIL - connection refused.")
        
        mock_git.invoke = MagicMock(return_value="Git response")

        graphs_loader = GraphsLoader()
        graph = graphs_loader.get_graph("coding")["graph"]
        
        inputs = {
            "messages": [],
            "query": "Implement database connection",
            "session_id": "test_session",
            "repo_path": self.repo_path,
            "max_retries": 2,
            "max_concurrency": 1
        }
        
        result = await graph.ainvoke(inputs)
        
        # With max_retries = 2, coder and QA should be called twice
        self.assertEqual(self.mock_coder.execute.call_count, 2)
        self.assertEqual(self.mock_qa.execute.call_count, 2)
        mock_git.invoke.assert_not_called()
        
        # Final message should be abort error message
        self.assertIn("Execution aborted due to task failure(s)", result["messages"][-1].content)
        self.assertIn("QA failed after 2 attempts", result["messages"][-1].content)
        self.assertEqual(len(result["completed_tasks"]), 0)
        self.assertEqual(len(result["failed_tasks"]), 1)

if __name__ == "__main__":
    unittest.main()
