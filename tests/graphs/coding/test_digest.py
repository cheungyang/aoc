"""Evidence that a worktree still holds the work we think it holds.

The digest is what lets a resumed tick skip an expensive LLM step. The contract
is that it only ever changes when the tree changes, and that an absent or
unreadable worktree yields no digest at all rather than a matching one — a
false match silently skips implementation work that was never written.
"""
import unittest
from unittest.mock import AsyncMock, patch

from graphs.coding.utils.digest import compute_worktree_digest, digest_matches


class TestWorktreeDigest(unittest.IsolatedAsyncioTestCase):
    @patch('graphs.coding.utils.digest.git_ops.run_cmd_async', new_callable=AsyncMock)
    async def test_same_tree_same_digest(self, mock_run):
        mock_run.side_effect = [(0, "diff-a", ""), (0, " M a.py", "")] * 2
        first = await compute_worktree_digest("/ws")
        second = await compute_worktree_digest("/ws")
        self.assertEqual(first, second)

    @patch('graphs.coding.utils.digest.git_ops.run_cmd_async', new_callable=AsyncMock)
    async def test_changed_tree_changes_digest(self, mock_run):
        mock_run.side_effect = [(0, "diff-a", ""), (0, " M a.py", ""),
                                (0, "diff-b", ""), (0, " M a.py", "")]
        self.assertNotEqual(await compute_worktree_digest("/ws"),
                            await compute_worktree_digest("/ws"))

    @patch('graphs.coding.utils.digest.git_ops.run_cmd_async', new_callable=AsyncMock)
    async def test_untracked_files_count(self, mock_run):
        # A brand-new file produces no diff at all; without the status line the
        # digest would claim two very different trees are identical.
        mock_run.side_effect = [(0, "", ""), (0, "?? new.py", ""),
                                (0, "", ""), (0, "?? other.py", "")]
        self.assertNotEqual(await compute_worktree_digest("/ws"),
                            await compute_worktree_digest("/ws"))

    @patch('graphs.coding.utils.digest.git_ops.run_cmd_async', new_callable=AsyncMock)
    async def test_git_failure_yields_no_evidence(self, mock_run):
        mock_run.return_value = (128, "", "not a git repository")
        self.assertIsNone(await compute_worktree_digest("/ws"))

    async def test_missing_workspace_yields_no_evidence(self):
        self.assertIsNone(await compute_worktree_digest(""))

    @patch('graphs.coding.utils.digest.git_ops.run_cmd_async', new_callable=AsyncMock)
    async def test_digest_matches_is_false_without_evidence(self, mock_run):
        mock_run.return_value = (128, "", "boom")
        # No digest must never read as "matches", or the LLM step would be
        # skipped for work that is not there.
        self.assertFalse(await digest_matches("/ws", "abc123"))
        self.assertFalse(await digest_matches("/ws", None))


if __name__ == "__main__":
    unittest.main()
