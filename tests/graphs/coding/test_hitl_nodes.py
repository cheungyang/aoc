import unittest
import os
import tempfile
import json
from unittest.mock import patch, AsyncMock
from graphs.coding.schemas import CodingState
from graphs.coding.nodes.hitl_gate import hitl_gate_node, process_hitl_decision_node, classify_hitl_intent


class TestHitlNodes(unittest.IsolatedAsyncioTestCase):
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

    @patch('graphs.coding.nodes.hitl_gate.git_ops.create_pull_request', new_callable=AsyncMock)
    @patch('graphs.coding.nodes.hitl_gate.git_ops.commit_and_push', new_callable=AsyncMock)
    async def test_hitl_gate_reloads_queue_from_disk(self, mock_commit, mock_pr):
        mock_commit.return_value = (True, "Committed")
        mock_pr.return_value = (True, "https://github.com/org/repo/pull/10", 10)

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            manifest_path = tf.name

        try:
            disk_manifest = {
                "version": "2.0",
                "project_name": "test_sync",
                "max_concurrency": 1,
                "queue": [
                    {"task_id": "TASK-01", "status": "pending"},
                    {"task_id": "TASK-02", "status": "pending"}
                ]
            }
            with open(manifest_path, "w") as f:
                json.dump(disk_manifest, f)

            stale_state: CodingState = {
                "workspace_path": "/tmp/ws",
                "branch_name": "feat/test_sync/task1",
                "run_id": "run_111",
                "build_request_path": manifest_path,
                "current_task": {"task_id": "TASK-01"},
                "queue": [{"task_id": "TASK-01", "status": "pending"}]
            }

            res = await hitl_gate_node(stale_state)
            self.assertEqual(len(res["queue"]), 2)
            self.assertEqual(res["queue"][0]["status"], "in_review")
            self.assertEqual(res["queue"][1]["task_id"], "TASK-02")

            with open(manifest_path, "r") as f:
                saved = json.load(f)
            self.assertEqual(len(saved["queue"]), 2)
            self.assertEqual(saved["queue"][0]["status"], "in_review")
            self.assertEqual(saved["queue"][1]["task_id"], "TASK-02")
        finally:
            if os.path.exists(manifest_path):
                os.remove(manifest_path)


if __name__ == "__main__":
    unittest.main()
