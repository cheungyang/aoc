import unittest
import os
import tempfile
import json
from unittest.mock import patch, AsyncMock, MagicMock
from langgraph.checkpoint.memory import MemorySaver
from core.loaders.graphs_loader import GraphsLoader
from graphs.coding.graph import create_graph
from graphs.coding.adapters import prepare_input, format_output, format_hitl_presentation
from tools.spec_validator import spec_validator

class TestCodingGraphE2E(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manifest_path = os.path.join(self.temp_dir.name, "build_request.json")
        self.spec_path = os.path.join(self.temp_dir.name, "specs", "auth.md")
        os.makedirs(os.path.dirname(self.spec_path), exist_ok=True)
        
        with open(self.spec_path, "w") as f:
            f.write("# Auth Spec\nAllowed files: auth.py\nSchema: AuthToken\nGiven auth When token Then ok\nVerification: pytest")

        self.manifest_data = {
            "version": "1.0",
            "project_name": "test_auth_app",
            "max_concurrency": 1,
            "queue": [
                {
                    "task_id": "TASK-01",
                    "project_name": "test_auth_app",
                    "feature_name": "jwt_auth",
                    "spec_path": self.spec_path,
                    "dependencies": [],
                    "allowed_files": ["auth.py"],
                    "verification_command": "pytest",
                    "acceptance_criteria": "Given valid token When verified Then True",
                    "status": "pending"
                }
            ]
        }
        with open(self.manifest_path, "w") as f:
            json.dump(self.manifest_data, f)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_graphs_loader_registers_coding(self):
        loader = GraphsLoader()
        info = loader.get_graph("coding")
        self.assertIsNotNone(info)
        self.assertEqual(info["config"]["graph_id"], "coding")
        self.assertIsNotNone(info["create_graph"])
        self.assertIsNotNone(info["prepare_input"])
        self.assertIsNotNone(info["format_output"])

    def test_missing_project_path_fails_initialization_fast(self):
        """Verifies that missing project_path does not infer or guess and fails immediately."""
        inputs = prepare_input(query="Run coding build request without dir")
        self.assertIn("Initialization error: 'project_path' is required", inputs["error_message"])

    @patch('graphs.coding.nodes.hitl_gate.git_ops.create_pull_request', new_callable=AsyncMock)
    @patch('graphs.coding.nodes.hitl_gate.git_ops.commit_and_push', new_callable=AsyncMock)
    @patch('graphs.coding.nodes.git_handoff.git_ops.merge_pull_request', new_callable=AsyncMock)
    @patch('graphs.coding.nodes.git_handoff.git_ops.teardown_worktree', new_callable=AsyncMock)
    @patch('graphs.coding.nodes.critic_node.git_ops.get_git_diff', new_callable=AsyncMock)
    async def test_full_pipeline_happy_path(
        self,
        mock_diff,
        mock_teardown,
        mock_merge,
        mock_commit,
        mock_pr
    ):
        mock_diff.return_value = "diff --git a/auth.py b/auth.py\n+ def verify(): return True"
        mock_commit.return_value = (True, "Committed")
        mock_pr.return_value = (True, "https://github.com/org/repo/pull/101", 101)
        mock_merge.return_value = (True, "https://github.com/org/repo/commit/sha_commit_101", "Merged successfully")
        mock_teardown.return_value = (True, "Teardown complete")

        async def fake_provision(repo_path, workspace_path, branch_name, base_ref=None):
            os.makedirs(workspace_path, exist_ok=True)
            return (True, "Worktree provisioned")

        with patch("graphs.coding.nodes.provisioner.git_ops.provision_worktree", side_effect=fake_provision), \
             patch("tools.agent_call.agent_call") as mock_agent:

            # Mock agent responses:
            # 1. Worker -> READY_FOR_TEST
            # 2. Critic -> APPROVE
            mock_agent.ainvoke = AsyncMock(side_effect=[
                "<worker_handoff><status>READY_FOR_TEST</status><modified_files><file>auth.py</file></modified_files><implementation_summary>Implemented JWT.</implementation_summary></worker_handoff>",
                "<critic_verdict><verdict>APPROVE</verdict><anti_patterns_detected></anti_patterns_detected><feedback_for_worker>Audited cleanly.</feedback_for_worker></critic_verdict>"
            ])

            from langgraph.checkpoint.memory import MemorySaver
            cp = MemorySaver()
            graph = create_graph(checkpointer=cp)

            inputs = prepare_input(
                query="Run coding build request",
                build_request_path=self.manifest_path,
                project_path=self.temp_dir.name,
                session_id="session_test_01",
                thread_id="thread_test_01"
            )
            config = {"configurable": {"thread_id": "thread_test_01"}}

            # 1. Run until HITL Gate interrupt
            with patch('graphs.coding.nodes.tester_node.asyncio.create_subprocess_shell') as mock_subproc:
                mock_proc = MagicMock()
                mock_proc.returncode = 0
                mock_proc.communicate = AsyncMock(return_value=(b"1 passed in 0.05s", b""))
                mock_subproc.return_value = mock_proc

                state_paused = await graph.ainvoke(inputs, config=config)

            self.assertTrue(state_paused["critic_passed"])
            self.assertTrue(state_paused["test_run_passed"])
            self.assertEqual(state_paused["hitl_decision"], "pending_review")
            # v2 Delta: PR created before pause
            self.assertEqual(state_paused["pr_url"], "https://github.com/org/repo/pull/101")
            self.assertEqual(state_paused["pr_number"], 101)

            # Presentation formatter check
            presentation = format_output(state_paused)
            self.assertIn("HITL Review Gate", presentation)
            self.assertIn("TASK-01", presentation)
            self.assertIn("https://github.com/org/repo/pull/101", presentation)

            # 2. Resume with user approval via update_state
            await graph.aupdate_state(config, {"latest_human_feedback": "Approve, looks great", "hitl_decision": "approved"})
            final_state = await graph.ainvoke(None, config=config)

            self.assertEqual(final_state["pr_url"], "https://github.com/org/repo/pull/101")
            self.assertEqual(final_state["commit_url"], "https://github.com/org/repo/commit/sha_commit_101")
            self.assertIn("TASK-01", final_state["completed_tasks"])
            
            output_str = format_output(final_state)
            self.assertIn("Coding Execution Completed & Merged!", output_str)
            self.assertIn("https://github.com/org/repo/pull/101", output_str)
            self.assertIn("sha_commit_101", output_str)

    @patch('graphs.coding.nodes.hitl_gate.git_ops.get_pull_request_status', new_callable=AsyncMock)
    @patch('graphs.coding.nodes.hitl_gate.git_ops.create_pull_request', new_callable=AsyncMock)
    @patch('graphs.coding.nodes.hitl_gate.git_ops.commit_and_push', new_callable=AsyncMock)
    @patch('graphs.coding.nodes.git_handoff.git_ops.merge_pull_request', new_callable=AsyncMock)
    @patch('graphs.coding.nodes.git_handoff.git_ops.teardown_worktree', new_callable=AsyncMock)
    @patch('graphs.coding.nodes.critic_node.git_ops.get_git_diff', new_callable=AsyncMock)
    async def test_dual_approval_via_github_pr_ui(
        self,
        mock_diff,
        mock_teardown,
        mock_merge,
        mock_commit,
        mock_pr,
        mock_pr_status
    ):
        mock_diff.return_value = "diff --git a/auth.py b/auth.py\n+ def verify(): return True"
        mock_commit.return_value = (True, "Committed")
        mock_pr.return_value = (True, "https://github.com/org/repo/pull/202", 202)
        mock_merge.return_value = (True, "https://github.com/org/repo/commit/sha_202", "Merged")
        mock_teardown.return_value = (True, "Teardown complete")
        mock_pr_status.return_value = {
            "state": "OPEN",
            "reviewDecision": "APPROVED",
            "comments": []
        }

        async def fake_provision(repo_path, workspace_path, branch_name, base_ref=None):
            os.makedirs(workspace_path, exist_ok=True)
            return (True, "Worktree provisioned")

        with patch("graphs.coding.nodes.provisioner.git_ops.provision_worktree", side_effect=fake_provision), \
             patch("tools.agent_call.agent_call") as mock_agent:

            mock_agent.ainvoke = AsyncMock(side_effect=[
                "<worker_handoff><status>READY_FOR_TEST</status><modified_files><file>auth.py</file></modified_files><implementation_summary>Done.</implementation_summary></worker_handoff>",
                "<critic_verdict><verdict>APPROVE</verdict><anti_patterns_detected></anti_patterns_detected><feedback_for_worker>Good.</feedback_for_worker></critic_verdict>"
            ])

            cp = MemorySaver()
            graph = create_graph(checkpointer=cp)

            inputs = prepare_input(
                query="Run coding build request",
                build_request_path=self.manifest_path,
                project_path=self.temp_dir.name,
                thread_id="thread_dual_app"
            )
            config = {"configurable": {"thread_id": "thread_dual_app"}}

            with patch('graphs.coding.nodes.tester_node.asyncio.create_subprocess_shell') as mock_subproc:
                mock_proc = MagicMock()
                mock_proc.returncode = 0
                mock_proc.communicate = AsyncMock(return_value=(b"1 passed", b""))
                mock_subproc.return_value = mock_proc

                state_paused = await graph.ainvoke(inputs, config=config)

            self.assertEqual(state_paused["hitl_decision"], "pending_review")

            # Resume with NO chat feedback: GitHub PR has reviewDecision: APPROVED
            await graph.aupdate_state(config, {"latest_human_feedback": ""})
            final_state = await graph.ainvoke(None, config=config)

            self.assertIn("TASK-01", final_state["completed_tasks"])
            self.assertEqual(final_state["commit_url"], "https://github.com/org/repo/commit/sha_202")

    async def test_elephant_spec_validator_utility(self):
        with patch("tools.agent_call.agent_call") as mock_agent:
            mock_agent.ainvoke = AsyncMock(return_value="""
            <spec_validation_result>
              <verdict>PASS</verdict>
              <unambiguous>true</unambiguous>
              <missing_assumptions></missing_assumptions>
              <summary>Spec is 100% self-contained.</summary>
            </spec_validation_result>
            """)
            res = await spec_validator.ainvoke({"spec_path": "specs/auth.md", "project_path": self.temp_dir.name})
            self.assertIn("<verdict>PASS</verdict>", res)

    async def test_dependent_branch_inheritance(self):
        # Multi-task queue where Task 2 depends on Task 1
        multi_manifest = {
            "version": "2.0",
            "project_name": "multi_test",
            "max_concurrency": 1,
            "queue": [
                {
                    "task_id": "TASK-01",
                    "project_name": "multi_test",
                    "feature_name": "db",
                    "spec_path": self.spec_path,
                    "dependencies": [],
                    "allowed_files": ["db.py"],
                    "verification_command": "pytest",
                    "acceptance_criteria": "db ok",
                    "status": "completed",
                    "branch_name": "feat/multi_test/db_run_1"
                },
                {
                    "task_id": "TASK-02",
                    "project_name": "multi_test",
                    "feature_name": "api",
                    "spec_path": self.spec_path,
                    "dependencies": ["TASK-01"],
                    "allowed_files": ["api.py"],
                    "verification_command": "pytest",
                    "acceptance_criteria": "api ok",
                    "status": "pending"
                }
            ]
        }
        manifest_file = os.path.join(self.temp_dir.name, "multi_manifest.json")
        with open(manifest_file, "w") as f:
            json.dump(multi_manifest, f)

        captured_base_branch = None
        captured_ws_path = None
        async def fake_provision(repo_path, workspace_path, branch_name, base_ref=None):
            nonlocal captured_base_branch, captured_ws_path
            captured_base_branch = base_ref
            captured_ws_path = workspace_path
            os.makedirs(workspace_path, exist_ok=True)
            return (True, "Worktree provisioned")

        with patch("graphs.coding.nodes.provisioner.git_ops.provision_worktree", side_effect=fake_provision), \
             patch("graphs.coding.nodes.critic_node.git_ops.get_git_diff", new_callable=AsyncMock) as mock_diff, \
             patch("graphs.coding.nodes.hitl_gate.git_ops.create_pull_request", new_callable=AsyncMock) as mock_pr, \
             patch("graphs.coding.nodes.hitl_gate.git_ops.commit_and_push", new_callable=AsyncMock) as mock_commit, \
             patch("graphs.coding.nodes.git_handoff.git_ops.merge_pull_request", new_callable=AsyncMock) as mock_merge, \
             patch("graphs.coding.nodes.git_handoff.git_ops.teardown_worktree", new_callable=AsyncMock) as mock_teardown, \
             patch("tools.agent_call.agent_call") as mock_agent:

            mock_diff.return_value = "diff --git a/api.py b/api.py\n+ def api(): pass"
            mock_commit.return_value = (True, "Committed")
            mock_pr.return_value = (True, "https://github.com/org/repo/pull/2", 2)
            mock_merge.return_value = (True, "https://github.com/org/repo/commit/sha_merge_2", "Merged")
            mock_agent.ainvoke = AsyncMock(side_effect=[
                "<worker_handoff><status>READY_FOR_TEST</status><modified_files><file>api.py</file></modified_files><implementation_summary>API done.</implementation_summary></worker_handoff>",
                "<critic_verdict><verdict>APPROVE</verdict><anti_patterns_detected></anti_patterns_detected><feedback_for_worker>Good.</feedback_for_worker></critic_verdict>"
            ])

            graph = create_graph(checkpointer=MemorySaver())
            inputs = prepare_input(
                query="Run multi build",
                build_request_path=manifest_file,
                project_path=self.temp_dir.name,
                thread_id="thread_multi_01"
            )
            config = {"configurable": {"thread_id": "thread_multi_01"}}

            with patch('graphs.coding.nodes.tester_node.asyncio.create_subprocess_shell') as mock_subproc:
                mock_proc = MagicMock()
                mock_proc.returncode = 0
                mock_proc.communicate = AsyncMock(return_value=(b"1 passed", b""))
                mock_subproc.return_value = mock_proc

                paused_state = await graph.ainvoke(inputs, config=config)

            self.assertEqual(captured_base_branch, "feat/multi_test/db_run_1")
            self.assertEqual(paused_state["current_task"]["task_id"], "TASK-02")
            # Workspace is decoupled outside pkm/
            self.assertIn("/workspaces/runs/", captured_ws_path)
            self.assertNotIn("/pkm/workspaces/", captured_ws_path)

if __name__ == "__main__":
    unittest.main()
