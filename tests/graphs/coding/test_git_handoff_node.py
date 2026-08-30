import unittest
import tempfile
import json
import os
from unittest.mock import patch, AsyncMock
from graphs.coding.schemas import CodingState
from graphs.coding.nodes.git_handoff import git_handoff_node


class TestGitHandoffNode(unittest.IsolatedAsyncioTestCase):
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

    @patch('graphs.coding.nodes.git_handoff.git_ops.merge_pull_request', new_callable=AsyncMock)
    @patch('graphs.coding.nodes.git_handoff.git_ops.teardown_worktree', new_callable=AsyncMock)
    async def test_git_handoff_reloads_queue_from_disk(self, mock_teardown, mock_merge):
        mock_merge.return_value = (True, "https://github.com/org/repo/commit/sha_merged_99", "Merged")
        mock_teardown.return_value = (True, "Cleaned")

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            manifest_path = tf.name

        try:
            disk_manifest = {
                "version": "2.0",
                "project_name": "test_sync",
                "max_concurrency": 1,
                "queue": [
                    {"task_id": "TASK-01", "status": "in_review", "pr_url": "https://github.com/org/repo/pull/99"},
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
                "pr_url": "https://github.com/org/repo/pull/99",
                "current_task": {"task_id": "TASK-01", "project_name": "test_sync"},
                "queue": [{"task_id": "TASK-01", "status": "in_review"}]
            }

            res = await git_handoff_node(stale_state)
            self.assertEqual(len(res["queue"]), 2)
            self.assertEqual(res["queue"][0]["status"], "completed")
            self.assertEqual(res["queue"][1]["task_id"], "TASK-02")

            with open(manifest_path, "r") as f:
                saved = json.load(f)
            self.assertEqual(len(saved["queue"]), 2)
            self.assertEqual(saved["queue"][0]["status"], "completed")
            self.assertEqual(saved["queue"][1]["task_id"], "TASK-02")
        finally:
            if os.path.exists(manifest_path):
                os.remove(manifest_path)


if __name__ == "__main__":
    unittest.main()
