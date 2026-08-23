import unittest
import os
import tempfile
import json
from unittest.mock import patch, AsyncMock, MagicMock
from graphs.coding.utils.git_ops import (
    run_cmd_async,
    run_cmd_sync,
    resolve_base_ref,
    provision_worktree,
    get_git_diff,
    commit_and_push,
    create_pull_request,
    get_pull_request_status,
    merge_pull_request,
    teardown_worktree
)

class TestGitOps(unittest.IsolatedAsyncioTestCase):
    @patch('graphs.coding.utils.git_ops.run_cmd_async')
    async def test_commit_and_push_with_author(self, mock_run):
        mock_run.side_effect = [
            (0, "", ""),  # git add .
            (0, "[main 12345] feat: done", ""),  # git commit
            (0, "To origin...", "")  # git push
        ]
        with tempfile.TemporaryDirectory() as tmp_ws:
            ok, msg = await commit_and_push(
                workspace_path=tmp_ws,
                branch_name="feat/test/auth_run_1",
                commit_msg="feat(test): implement auth",
                author="Graph Worker <worker@egm.internal>"
            )
            self.assertTrue(ok)
            self.assertIn("Successfully committed and pushed", msg)
            # Verify --author was in commit command
            commit_call = mock_run.call_args_list[1][0][0]
            self.assertIn("--author=Graph Worker <worker@egm.internal>", commit_call)

    @patch('graphs.coding.utils.git_ops.run_cmd_async')
    async def test_create_pull_request_success(self, mock_run):
        mock_run.return_value = (0, "https://github.com/my-org/my-repo/pull/142\n", "")
        ok, pr_url, pr_num = await create_pull_request(
            workspace_path="/tmp/ws",
            branch_name="feat/auth_1",
            title="feat: auth",
            body="Automated PR",
            base_branch="main"
        )
        self.assertTrue(ok)
        self.assertEqual(pr_url, "https://github.com/my-org/my-repo/pull/142")
        self.assertEqual(pr_num, 142)

    @patch('graphs.coding.utils.git_ops.run_cmd_async')
    async def test_create_pull_request_fallback(self, mock_run):
        mock_run.return_value = (1, "", "fatal: not logged in to gh")
        ok, pr_url, pr_num = await create_pull_request(
            workspace_path="/tmp/ws",
            branch_name="feat/auth_1",
            title="feat: auth",
            body="Automated PR"
        )
        self.assertTrue(ok)
        self.assertIn("https://github.com/local-repo/pull/feat/auth_1", pr_url)
        self.assertIsNone(pr_num)

    @patch('graphs.coding.utils.git_ops.run_cmd_async')
    async def test_get_pull_request_status_success(self, mock_run):
        mock_data = {
            "state": "OPEN",
            "reviewDecision": "APPROVED",
            "comments": [{"body": "Looks great!"}],
            "url": "https://github.com/org/repo/pull/42",
            "number": 42
        }
        mock_run.return_value = (0, json.dumps(mock_data), "")
        status = await get_pull_request_status("/tmp/ws", 42)
        self.assertEqual(status["reviewDecision"], "APPROVED")
        self.assertEqual(len(status["comments"]), 1)
        self.assertEqual(status["comments"][0]["body"], "Looks great!")

    @patch('graphs.coding.utils.git_ops.run_cmd_async')
    async def test_get_pull_request_status_failure_fallback(self, mock_run):
        mock_run.return_value = (1, "", "gh: command not found")
        status = await get_pull_request_status("/tmp/ws", 42)
        self.assertEqual(status["state"], "OPEN")
        self.assertEqual(status["reviewDecision"], "")

    @patch('graphs.coding.utils.git_ops.get_pull_request_status', new_callable=AsyncMock)
    @patch('graphs.coding.utils.git_ops.run_cmd_async')
    async def test_merge_pull_request_success(self, mock_run, mock_status):
        mock_run.return_value = (0, "Merged pull request #42", "")
        mock_status.return_value = {
            "url": "https://github.com/org/repo/pull/42",
            "mergeCommit": {"oid": "a1b2c3d4e5f6"}
        }
        ok, commit_url, msg = await merge_pull_request("/tmp/ws", 42, squash=True, delete_branch=True)
        self.assertTrue(ok)
        self.assertEqual(commit_url, "https://github.com/org/repo/commit/a1b2c3d4e5f6")
        self.assertIn("Merged", msg)

    @patch('graphs.coding.utils.git_ops.run_cmd_async')
    async def test_teardown_worktree(self, mock_run):
        mock_run.return_value = (0, "", "")
        with tempfile.TemporaryDirectory() as tmp_repo:
            with tempfile.TemporaryDirectory() as tmp_ws:
                ok, msg = await teardown_worktree(tmp_repo, tmp_ws)
                self.assertTrue(ok)
                self.assertIn("Teardown complete", msg)

if __name__ == '__main__':
    unittest.main()
