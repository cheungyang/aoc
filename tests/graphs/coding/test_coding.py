import unittest
import os
import sys
import tempfile
import json
from unittest.mock import patch, AsyncMock, MagicMock

# Add root directory to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.loaders.graphs_loader import GraphsLoader
from graphs.coding.graph import create_graph
from graphs.coding.adapters import prepare_input, format_output

class TestCodingSubgraph(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manifest_path = os.path.join(self.temp_dir.name, "build_request.json")
        self.spec_path = os.path.join(self.temp_dir.name, "specs", "math.md")
        os.makedirs(os.path.dirname(self.spec_path), exist_ok=True)
        
        with open(self.spec_path, "w") as f:
            f.write("# Math Spec\nAllowed files: math_utils.py\nSchema: Int\nGiven add(1, 2) When called Then 3\nVerification: pytest")

        self.manifest_data = {
            "version": "1.0",
            "project_name": "math_pkg",
            "max_concurrency": 1,
            "queue": [
                {
                    "task_id": "TASK-MATH-01",
                    "project_name": "math_pkg",
                    "feature_name": "add_func",
                    "spec_path": self.spec_path,
                    "dependencies": [],
                    "allowed_files": ["math_utils.py"],
                    "verification_command": "pytest",
                    "acceptance_criteria": "add(1, 2) returns 3",
                    "status": "pending"
                }
            ]
        }
        with open(self.manifest_path, "w") as f:
            json.dump(self.manifest_data, f)

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch('graphs.coding.nodes.git_handoff.git_ops.create_pull_request', new_callable=AsyncMock)
    @patch('graphs.coding.nodes.git_handoff.git_ops.commit_and_push', new_callable=AsyncMock)
    @patch('graphs.coding.nodes.git_handoff.git_ops.teardown_worktree', new_callable=AsyncMock)
    @patch('graphs.coding.nodes.critic_node.git_ops.get_git_diff', new_callable=AsyncMock)
    async def test_coding_subgraph_success(
        self,
        mock_diff,
        mock_teardown,
        mock_commit,
        mock_pr
    ):
        mock_diff.return_value = "diff --git a/math_utils.py b/math_utils.py\n+ def add(a, b): return a + b"
        mock_commit.return_value = (True, "Committed")
        mock_pr.return_value = (True, "https://github.com/org/repo/pull/1")
        mock_teardown.return_value = (True, "Cleaned")

        async def fake_provision(repo_path, workspace_path, branch_name, base_ref=None):
            os.makedirs(workspace_path, exist_ok=True)
            return (True, "Worktree provisioned")

        with patch("graphs.coding.nodes.provisioner.git_ops.provision_worktree", side_effect=fake_provision), \
             patch("tools.agent_call.agent_call") as mock_agent:

            mock_agent.ainvoke = AsyncMock(side_effect=[
                "<worker_handoff><status>READY_FOR_TEST</status><modified_files><file>math_utils.py</file></modified_files><implementation_summary>Implemented add function.</implementation_summary></worker_handoff>",
                "<critic_verdict><verdict>APPROVE</verdict><anti_patterns_detected></anti_patterns_detected><feedback_for_worker>Good.</feedback_for_worker></critic_verdict>"
            ])

            from langgraph.checkpoint.memory import MemorySaver
            graph = create_graph(checkpointer=MemorySaver())

            inputs = prepare_input(
                query="Run build",
                build_request_path=self.manifest_path,
                project_path=self.temp_dir.name,
                thread_id="test_thread_math"
            )
            config = {"configurable": {"thread_id": "test_thread_math"}}

            # Mock tester passing
            with patch('graphs.coding.nodes.tester_node.asyncio.create_subprocess_shell') as mock_subproc:
                mock_proc = MagicMock()
                mock_proc.returncode = 0
                mock_proc.communicate = AsyncMock(return_value=(b"1 passed", b""))
                mock_subproc.return_value = mock_proc

                paused_state = await graph.ainvoke(inputs, config=config)

            self.assertTrue(paused_state["test_run_passed"])
            self.assertTrue(paused_state["critic_passed"])

            # Resume with approval via aupdate_state
            await graph.aupdate_state(config, {"latest_human_feedback": "Looks great, approve", "hitl_decision": "approved"})
            final_state = await graph.ainvoke(None, config=config)

            self.assertIn("TASK-MATH-01", final_state["completed_tasks"])
            self.assertEqual(final_state["pr_url"], "https://github.com/org/repo/pull/1")

    async def test_coding_subgraph_retry_and_fail(self):
        async def fake_provision(repo_path, workspace_path, branch_name, base_ref=None):
            os.makedirs(workspace_path, exist_ok=True)
            return (True, "Worktree provisioned")

        with patch("graphs.coding.nodes.provisioner.git_ops.provision_worktree", side_effect=fake_provision), \
             patch("tools.agent_call.agent_call") as mock_agent:

            mock_agent.ainvoke = AsyncMock(side_effect=[
                # Worker Attempt 1
                "<worker_handoff><status>READY_FOR_TEST</status><modified_files><file>math_utils.py</file></modified_files><implementation_summary>Attempt 1.</implementation_summary></worker_handoff>",
                # Worker Attempt 2 (Retry)
                "<worker_handoff><status>READY_FOR_TEST</status><modified_files><file>math_utils.py</file></modified_files><implementation_summary>Attempt 2.</implementation_summary></worker_handoff>",
                # Worker Attempt 3 (Retry)
                "<worker_handoff><status>READY_FOR_TEST</status><modified_files><file>math_utils.py</file></modified_files><implementation_summary>Attempt 3.</implementation_summary></worker_handoff>"
            ])

            from langgraph.checkpoint.memory import MemorySaver
            graph = create_graph(checkpointer=MemorySaver())

            inputs = prepare_input(
                query="Run build",
                build_request_path=self.manifest_path,
                project_path=self.temp_dir.name,
                max_retries=2,
                thread_id="test_fail_thread"
            )
            config = {"configurable": {"thread_id": "test_fail_thread"}}

            # Tester always fails (returncode = 1)
            with patch('graphs.coding.nodes.tester_node.asyncio.create_subprocess_shell') as mock_subproc:
                mock_proc = MagicMock()
                mock_proc.returncode = 1
                mock_proc.communicate = AsyncMock(return_value=(b"", b"AssertionError: 1 != 2"))
                mock_subproc.return_value = mock_proc

                failed_state = await graph.ainvoke(inputs, config=config)

            self.assertFalse(failed_state["test_run_passed"])
            self.assertGreaterEqual(failed_state["attempt_count"], 2)
            self.assertNotIn("pr_url", failed_state)

if __name__ == "__main__":
    unittest.main()
