import unittest
import os
import tempfile
import json
from unittest.mock import patch, AsyncMock, MagicMock
from graphs.coding.schemas import CodingState, TaskEnvelope
from graphs.coding.nodes.dag_scheduler import dag_scheduler_node
from graphs.coding.nodes.spec_validator import spec_validator_node
from graphs.coding.nodes.provisioner import provisioner_node
from graphs.coding.nodes.worker_node import worker_node
from graphs.coding.nodes.tester_node import tester_node as run_tester_node
from graphs.coding.nodes.critic_node import critic_node
from graphs.coding.nodes.hitl_gate import hitl_gate_node, process_hitl_decision_node, classify_hitl_intent
from graphs.coding.nodes.git_handoff import git_handoff_node

class TestCodingNodes(unittest.IsolatedAsyncioTestCase):
    async def test_dag_scheduler_node(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            manifest_path = tf.name

        try:
            manifest_data = {
                "version": "1.0",
                "project_name": "test_project",
                "max_concurrency": 1,
                "queue": [
                    {
                        "task_id": "TASK-01",
                        "project_name": "test_project",
                        "feature_name": "auth",
                        "spec_path": "specs/auth.md",
                        "dependencies": [],
                        "allowed_files": ["auth.py"],
                        "verification_command": "pytest",
                        "acceptance_criteria": "Given auth When token Then ok",
                        "status": "pending"
                    }
                ]
            }
            with open(manifest_path, "w") as f:
                json.dump(manifest_data, f)

            state: CodingState = {
                "build_request_path": manifest_path,
                "project_name": "test_project",
                "project_path": os.path.dirname(manifest_path),
                "queue": manifest_data["queue"]
            }

            res = await dag_scheduler_node(state)
            self.assertIsNotNone(res["current_task"])
            self.assertEqual(res["current_task"]["task_id"], "TASK-01")
            self.assertTrue(res["run_id"].startswith("run_"))
            self.assertEqual(res["current_task"]["status"], "in_progress")
        finally:
            if os.path.exists(manifest_path):
                os.remove(manifest_path)

    async def test_spec_validator_node_pass(self):
        with patch("tools.agent_call.agent_call") as mock_agent:
            mock_agent.ainvoke = AsyncMock(return_value="""
            <spec_validation_result>
              <verdict>PASS</verdict>
              <unambiguous>true</unambiguous>
              <missing_assumptions></missing_assumptions>
              <summary>Spec is 100% complete.</summary>
            </spec_validation_result>
            """)
            with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tf:
                spec_file = tf.name
                tf.write(b"# Spec\nAllowed files: auth.py\nSchema: User\nGiven X When Y Then Z\nVerification: pytest")

            try:
                state: CodingState = {
                    "master_spec_path": spec_file,
                    "project_path": os.path.dirname(spec_file),
                    "current_task": {"task_id": "T-1", "spec_path": spec_file}
                }
                res = await spec_validator_node(state)
                self.assertTrue(res["spec_validation_passed"])
                self.assertIn("PASS", res["spec_validation_feedback"])
            finally:
                if os.path.exists(spec_file):
                    os.remove(spec_file)

    @patch('graphs.coding.nodes.provisioner.git_ops.provision_worktree', new_callable=AsyncMock)
    async def test_provisioner_node(self, mock_provision):
        mock_provision.return_value = (True, "Worktree provisioned")
        state: CodingState = {
            "run_id": "run_8F2A",
            "project_name": "cc-tracker",
            "current_task": {
                "task_id": "TASK-01",
                "feature_name": "auth",
                "project_name": "cc-tracker"
            },
            "project_path": "/tmp/repo"
        }
        res = await provisioner_node(state)
        self.assertIn("run_8F2A", res["workspace_path"])
        self.assertEqual(res["branch_name"], "feat/cc-tracker/auth_run_8F2A")
        self.assertEqual(res["error_message"], "")

    async def test_worker_node(self):
        with patch("tools.agent_call.agent_call") as mock_agent:
            mock_agent.ainvoke = AsyncMock(return_value="""
            <worker_handoff>
              <status>READY_FOR_TEST</status>
              <modified_files>
                <file>src/auth.py</file>
              </modified_files>
              <implementation_summary>Implemented auth handler.</implementation_summary>
            </worker_handoff>
            """)
            state: CodingState = {
                "workspace_path": "/tmp/ws",
                "current_task": {
                    "task_id": "TASK-01",
                    "allowed_files": ["src/auth.py"],
                    "acceptance_criteria": "Given token When verify Then True",
                    "verification_command": "pytest"
                }
            }
            res = await worker_node(state)
            self.assertEqual(res["modified_files"], ["src/auth.py"])
            self.assertIn("Implemented auth", res["implementation_summary"])

    async def test_tester_node_pass_and_fail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            # 1. Passing command
            state_pass: CodingState = {
                "workspace_path": temp_dir,
                "current_task": {"verification_command": "echo 'all tests pass'"},
                "attempt_count": 0
            }
            res_pass = await run_tester_node(state_pass)
            self.assertTrue(res_pass["test_run_passed"])
            self.assertEqual(res_pass["attempt_count"], 0)

            # 2. Failing command
            state_fail: CodingState = {
                "workspace_path": temp_dir,
                "current_task": {"verification_command": "sh -c 'exit 1'"},
                "attempt_count": 0
            }
            res_fail = await run_tester_node(state_fail)
            self.assertFalse(res_fail["test_run_passed"])
            self.assertEqual(res_fail["attempt_count"], 1)

    @patch('graphs.coding.nodes.critic_node.git_ops.get_git_diff', new_callable=AsyncMock)
    async def test_critic_node_approve(self, mock_diff):
        mock_diff.return_value = "diff --git a/test.py b/test.py\n+ def test(): pass"
        with patch("tools.agent_call.agent_call") as mock_agent:
            mock_agent.ainvoke = AsyncMock(return_value="""
            <critic_verdict>
              <verdict>APPROVE</verdict>
              <anti_patterns_detected></anti_patterns_detected>
              <feedback_for_worker>Looks good.</feedback_for_worker>
            </critic_verdict>
            """)
            state: CodingState = {
                "workspace_path": "/tmp/ws",
                "modified_files": ["test.py"],
                "current_task": {
                    "task_id": "T-1",
                    "acceptance_criteria": "Given test When run Then pass"
                }
            }
            res = await critic_node(state)
            self.assertTrue(res["critic_passed"])
            self.assertEqual(res["critic_feedback"], "")

    @patch('graphs.coding.nodes.critic_node.git_ops.get_git_diff', new_callable=AsyncMock)
    async def test_critic_node_fails_closed_when_agent_fails(self, mock_diff):
        mock_diff.return_value = "diff --git a/test.py b/test.py\n+ def test(): pass"
        with patch("tools.agent_call.agent_call") as mock_agent:
            mock_agent.ainvoke = AsyncMock(side_effect=RuntimeError("LLM timeout"))
            state: CodingState = {
                "workspace_path": "/tmp/ws",
                "modified_files": ["test.py"],
                "current_task": {
                    "task_id": "T-1",
                    "acceptance_criteria": "Given test When run Then pass"
                }
            }
            res = await critic_node(state)
            self.assertFalse(res["critic_passed"])
            self.assertIn("Critic QA audit failed to complete", res["critic_feedback"])

    def test_classify_hitl_intent(self):
        self.assertEqual(classify_hitl_intent("approved"), "approved")
        self.assertEqual(classify_hitl_intent("LGTM, proceed"), "approved")
        self.assertEqual(classify_hitl_intent("abort"), "abort")
        self.assertEqual(classify_hitl_intent("Add inline docstrings to line 30"), "revise")

    @patch('graphs.coding.nodes.hitl_gate.git_ops.create_pull_request', new_callable=AsyncMock)
    @patch('graphs.coding.nodes.hitl_gate.git_ops.commit_and_push', new_callable=AsyncMock)
    async def test_hitl_gate_and_processor(self, mock_commit, mock_pr):
        mock_commit.return_value = (True, "Committed")
        mock_pr.return_value = (True, "https://github.com/org/repo/pull/1", 1)

        state: CodingState = {
            "workspace_path": "/tmp/ws",
            "branch_name": "feat/proj/auth_run_1",
            "current_task": {"task_id": "TASK-01"},
            "latest_human_feedback": "Approved, looks clean to merge"
        }
        res_pause = await hitl_gate_node(state)
        self.assertEqual(res_pause["hitl_decision"], "pending_review")
        self.assertEqual(res_pause["pr_url"], "https://github.com/org/repo/pull/1")

        res_proc = await process_hitl_decision_node(state)
        self.assertEqual(res_proc["hitl_decision"], "approved")

        state_revise: CodingState = {
            "latest_human_feedback": "Please fix typo on line 12"
        }
        res_revise = await process_hitl_decision_node(state_revise)
        self.assertEqual(res_revise["hitl_decision"], "revise")
        self.assertEqual(res_revise["latest_human_feedback"], "Please fix typo on line 12")

    @patch('graphs.coding.nodes.git_handoff.git_ops.merge_pull_request', new_callable=AsyncMock)
    @patch('graphs.coding.nodes.git_handoff.git_ops.teardown_worktree', new_callable=AsyncMock)
    @patch('graphs.coding.nodes.git_handoff.save_manifest')
    async def test_git_handoff_node(self, mock_save, mock_teardown, mock_merge):
        mock_merge.return_value = (True, "https://github.com/org/repo/commit/sha_merged_42", "Merged")
        mock_teardown.return_value = (True, "Cleaned")
        mock_save.return_value = True

        state: CodingState = {
            "workspace_path": "/tmp/ws",
            "branch_name": "feat/proj/auth_run_8F2A",
            "run_id": "run_8F2A",
            "pr_url": "https://github.com/org/repo/pull/42",
            "current_task": {
                "task_id": "TASK-01",
                "feature_name": "auth",
                "project_name": "proj",
                "spec_path": "specs/auth.md",
                "status": "in_review",
                "pr_url": "https://github.com/org/repo/pull/42"
            },
            "queue": [
                {"task_id": "TASK-01", "status": "in_review", "pr_url": "https://github.com/org/repo/pull/42"}
            ],
            "completed_tasks": []
        }

        res = await git_handoff_node(state)
        self.assertEqual(res["pr_url"], "https://github.com/org/repo/pull/42")
        self.assertEqual(res["commit_url"], "https://github.com/org/repo/commit/sha_merged_42")
        self.assertIn("TASK-01", res["completed_tasks"])
        self.assertIsNone(res["current_task"])

if __name__ == "__main__":
    unittest.main()
