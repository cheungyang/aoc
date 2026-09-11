import unittest
from unittest.mock import AsyncMock, patch
import tempfile

from core.util.git_ops import (
    commit_and_push,
    comment_pull_request,
    create_pull_request,
    ensure_label,
    get_pull_request_status,
    merge_pull_request,
    preflight_push_access,
)
from core.util.push_identity import PushIdentity, TOKEN_ENV_VAR

BOT = PushIdentity(
    login="cheungyang-bot",
    email="cheungyang-bot@users.noreply.github.com",
    token="ghp_secret",
    token_path="/tmp/bot_token"
)


class TestCommitAndPushAsMachineUser(unittest.IsolatedAsyncioTestCase):
    @patch('core.util.git_ops.run_cmd_async')
    async def test_commit_attributed_to_bot_and_push_authenticated(self, mock_run):
        mock_run.side_effect = [
            (0, "", ""),                          # git add .
            (0, "[branch abc] feat: done", ""),   # git commit
            (0, "To origin...", "")               # git push
        ]
        with tempfile.TemporaryDirectory() as tmp_ws:
            ok, msg = await commit_and_push(
                workspace_path=tmp_ws,
                branch_name="feat/x/y_run_1",
                commit_msg="feat(x): implement y",
                author="Graph Worker <worker@egm.internal>",
                push_identity=BOT
            )

        self.assertTrue(ok)

        commit_cmd = mock_run.call_args_list[1][0][0]
        self.assertIn("user.name=cheungyang-bot", commit_cmd)
        # The machine user owns the history, so it overrides the caller's default author.
        self.assertIn(f"--author={BOT.author}", commit_cmd)

        push_call = mock_run.call_args_list[2]
        push_cmd = push_call[0][0]
        self.assertIn("credential.helper=", push_cmd)
        self.assertNotIn("ghp_secret", " ".join(push_cmd))
        self.assertEqual(push_call[1]["env"][TOKEN_ENV_VAR], "ghp_secret")

    @patch('core.util.git_ops.run_cmd_async')
    async def test_without_identity_nothing_changes(self, mock_run):
        mock_run.side_effect = [(0, "", ""), (0, "", ""), (0, "", "")]
        with tempfile.TemporaryDirectory() as tmp_ws:
            await commit_and_push(
                workspace_path=tmp_ws,
                branch_name="feat/x/y_run_1",
                commit_msg="feat(x): implement y"
            )

        push_cmd = mock_run.call_args_list[2][0][0]
        self.assertEqual(push_cmd[:2], ["git", "push"])
        self.assertNotIn(TOKEN_ENV_VAR, mock_run.call_args_list[2][1]["env"])


class TestGhOperationsCarryToken(unittest.IsolatedAsyncioTestCase):
    @patch('core.util.git_ops.discover_target_repo', new_callable=AsyncMock)
    @patch('core.util.git_ops.run_cmd_async')
    async def test_create_pull_request(self, mock_run, mock_discover):
        mock_discover.return_value = "owner/repo"
        mock_run.return_value = (0, "https://github.com/owner/repo/pull/7", "")
        ok, url, number = await create_pull_request(
            workspace_path=".", branch_name="b", title="t", body="b",
            push_identity=BOT
        )
        self.assertTrue(ok)
        self.assertEqual(number, 7)
        self.assertEqual(mock_run.call_args[1]["env"][TOKEN_ENV_VAR], "ghp_secret")

    @patch('core.util.git_ops.discover_target_repo', new_callable=AsyncMock)
    @patch('core.util.git_ops.run_cmd_async')
    async def test_get_pull_request_status(self, mock_run, mock_discover):
        mock_discover.return_value = "owner/repo"
        mock_run.return_value = (0, '{"state": "OPEN"}', "")
        await get_pull_request_status(".", "7", push_identity=BOT)
        self.assertEqual(mock_run.call_args[1]["env"][TOKEN_ENV_VAR], "ghp_secret")

    @patch('core.util.git_ops.get_pull_request_status', new_callable=AsyncMock)
    @patch('core.util.git_ops.discover_target_repo', new_callable=AsyncMock)
    @patch('core.util.git_ops.run_cmd_async')
    async def test_merge_pull_request(self, mock_run, mock_discover, mock_status):
        mock_discover.return_value = "owner/repo"
        mock_run.return_value = (0, "merged", "")
        mock_status.return_value = {"url": "https://github.com/owner/repo/pull/7",
                                    "mergeCommit": {"oid": "abc123"}}
        ok, commit_url, _ = await merge_pull_request(".", "7", push_identity=BOT)
        self.assertTrue(ok)
        self.assertIn("abc123", commit_url)
        self.assertEqual(mock_run.call_args[1]["env"][TOKEN_ENV_VAR], "ghp_secret")
        self.assertEqual(mock_status.call_args[1]["push_identity"], BOT)

    @patch('core.util.git_ops.discover_target_repo', new_callable=AsyncMock)
    @patch('core.util.git_ops.run_cmd_async')
    async def test_comment_pull_request(self, mock_run, mock_discover):
        mock_discover.return_value = "owner/repo"
        mock_run.return_value = (0, "", "")
        ok, _ = await comment_pull_request(".", "https://github.com/owner/repo/pull/7", "hi",
                                           push_identity=BOT)
        self.assertTrue(ok)
        self.assertEqual(mock_run.call_args[1]["env"][TOKEN_ENV_VAR], "ghp_secret")


class TestPreflightPushAccess(unittest.IsolatedAsyncioTestCase):
    @patch('core.util.git_ops.run_cmd_async')
    async def test_passes_when_bot_can_push(self, mock_run):
        mock_run.side_effect = [
            (0, "cheungyang-bot\n", ""),   # gh api user
            (0, "true\n", "")              # gh api repos/<slug> .permissions.push
        ]
        ok, msg = await preflight_push_access("owner/repo", push_identity=BOT)
        self.assertTrue(ok, msg)
        self.assertIn("can push", msg)

    @patch('core.util.git_ops.run_cmd_async')
    async def test_fails_when_unauthenticated(self, mock_run):
        mock_run.return_value = (1, "", "gh: not logged in")
        ok, msg = await preflight_push_access("owner/repo", push_identity=BOT)
        self.assertFalse(ok)
        self.assertIn("not authenticated", msg)

    @patch('core.util.git_ops.run_cmd_async')
    async def test_fails_when_token_belongs_to_someone_else(self, mock_run):
        # The whole point of the machine user is that the PR is not authored by the
        # reviewer; a token that resolves to the human silently defeats that.
        mock_run.side_effect = [(0, "cheungyang\n", "")]
        ok, msg = await preflight_push_access("owner/repo", push_identity=BOT)
        self.assertFalse(ok)
        self.assertIn("cheungyang-bot", msg)

    @patch('core.util.git_ops.run_cmd_async')
    async def test_fails_without_push_permission(self, mock_run):
        mock_run.side_effect = [
            (0, "cheungyang-bot\n", ""),
            (0, "false\n", "")
        ]
        ok, msg = await preflight_push_access("owner/repo", push_identity=BOT)
        self.assertFalse(ok)
        self.assertIn("no push permission", msg)

    @patch('core.util.git_ops.discover_target_repo', new_callable=AsyncMock)
    async def test_fails_without_a_repo(self, mock_discover):
        mock_discover.return_value = None
        ok, msg = await preflight_push_access(None)
        self.assertFalse(ok)
        self.assertIn("could not determine the target repository", msg)


class TestEnsureLabel(unittest.IsolatedAsyncioTestCase):
    @patch('core.util.git_ops.run_cmd_async')
    async def test_creates_label(self, mock_run):
        mock_run.return_value = (0, "", "")
        ok, msg = await ensure_label("owner/repo")
        self.assertTrue(ok)
        self.assertIn("Created label", msg)

    @patch('core.util.git_ops.run_cmd_async')
    async def test_existing_label_is_not_an_error(self, mock_run):
        mock_run.return_value = (1, "", "label already exists")
        ok, msg = await ensure_label("owner/repo")
        self.assertTrue(ok)
        self.assertIn("already exists", msg)


if __name__ == "__main__":
    unittest.main()
