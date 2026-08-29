import unittest
import os
import tempfile
import json
from unittest.mock import patch, AsyncMock
from graphs.coding.schemas import CodingState
from graphs.coding.nodes.dag_scheduler import dag_scheduler_node
from graphs.coding.nodes.spec_validator import spec_validator_node
from graphs.coding.nodes.provisioner import provisioner_node
from graphs.coding.nodes.worker_node import worker_node
from graphs.coding.nodes.tester_node import tester_node as run_tester_node


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
            mock_agent.ainvoke = AsyncMock(return_value="""<spec_validation_result>
  <verdict>PASS</verdict>
  <unambiguous>true</unambiguous>
  <missing_assumptions></missing_assumptions>
  <summary>Spec is 100% complete.</summary>
</spec_validation_result>""")
            with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tf:
                temp_spec_path = tf.name
                tf.write(b"# Spec\nAllowed files: auth.py\nSchema: User\nGiven X When Y Then Z\nVerification: pytest")

            try:
                state: CodingState = {
                    "spec_path": temp_spec_path,
                    "project_path": os.path.dirname(temp_spec_path),
                    "current_task": {"task_id": "T-1", "spec_path": temp_spec_path}
                }
                res = await spec_validator_node(state)
                self.assertTrue(res["spec_validation_passed"])
                self.assertIn("PASS", res["spec_validation_feedback"])
            finally:
                if os.path.exists(temp_spec_path):
                    os.remove(temp_spec_path)

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
            "project_path": "pkm/wiki/software/cc-tracker"
        }
        res = await provisioner_node(state)
        self.assertTrue(res["workspace_path"].endswith("workspaces/runs/run_8F2A"))
        self.assertNotIn("pkm", res["workspace_path"])
        self.assertEqual(res["branch_name"], "feat/cc-tracker/auth_run_8F2A")
        self.assertEqual(res["error_message"], "")

    async def test_worker_node(self):
        with patch("tools.agent_call.agent_call") as mock_agent:
            mock_agent.ainvoke = AsyncMock(return_value="""<worker_handoff>
  <status>READY_FOR_TEST</status>
  <modified_files><file>src/auth.py</file></modified_files>
  <implementation_summary>Implemented auth handler.</implementation_summary>
</worker_handoff>""")
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
            res_pass = await run_tester_node({
                "workspace_path": temp_dir,
                "current_task": {"verification_command": "echo 'all tests pass'"},
                "attempt_count": 0
            })
            self.assertTrue(res_pass["test_run_passed"])
            self.assertEqual(res_pass["attempt_count"], 0)

            res_fail = await run_tester_node({
                "workspace_path": temp_dir,
                "current_task": {"verification_command": "sh -c 'exit 1'"},
                "attempt_count": 0
            })
            self.assertFalse(res_fail["test_run_passed"])
            self.assertEqual(res_fail["attempt_count"], 1)


if __name__ == "__main__":
    unittest.main()
