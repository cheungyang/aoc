"""Unresolved inline review threads.

Inline comments are the usual way to ask for a change, and they are invisible to
`gh pr view --json comments`. Reading them from the thread API — and only the
threads nobody has resolved — is what stops the system from either ignoring a
review or re-actioning one that is already settled.
"""
import json
import unittest
from unittest.mock import AsyncMock, patch

from core.util.git_ops import get_unresolved_review_threads


def _payload(threads):
    return json.dumps({
        "data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": threads}}}}
    })


def _thread(body, resolved=False, outdated=False, login="alice", db_id=1, path="app.py", line=12):
    return {
        "isResolved": resolved,
        "isOutdated": outdated,
        "comments": {"nodes": [{
            "databaseId": db_id,
            "body": body,
            "path": path,
            "line": line,
            "author": {"login": login},
        }]},
    }


class TestUnresolvedReviewThreads(unittest.IsolatedAsyncioTestCase):
    async def _run(self, stdout, code=0, **kwargs):
        with patch("core.util.git_ops.run_cmd_async",
                   AsyncMock(return_value=(code, stdout, ""))) as mock_run:
            result = await get_unresolved_review_threads(
                kwargs.pop("workspace_path", "/ws"),
                target_repo=kwargs.pop("target_repo", "org/repo"),
                pr_number=kwargs.pop("pr_number", 7),
                **kwargs
            )
        return result, mock_run

    async def test_an_open_thread_is_returned(self):
        result, _ = await self._run(_payload([_thread("please rename this")]))

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["author"]["login"], "alice")
        self.assertEqual(result[0]["databaseId"], 1)

    async def test_the_file_and_line_are_prefixed_onto_the_body(self):
        """The worker needs to know where the comment points."""
        result, _ = await self._run(_payload([_thread("rename this", path="core/app.py", line=42)]))

        self.assertEqual(result[0]["body"], "[core/app.py:42] rename this")

    async def test_a_resolved_thread_is_dropped(self):
        result, _ = await self._run(_payload([_thread("old point", resolved=True)]))
        self.assertEqual(result, [])

    async def test_an_outdated_thread_is_dropped(self):
        result, _ = await self._run(_payload([_thread("stale point", outdated=True)]))
        self.assertEqual(result, [])

    async def test_mixed_threads_return_only_the_open_ones(self):
        result, _ = await self._run(_payload([
            _thread("resolved", resolved=True, db_id=1),
            _thread("still open", db_id=2),
            _thread("outdated", outdated=True, db_id=3),
        ]))

        self.assertEqual([c["databaseId"] for c in result], [2])

    async def test_the_query_targets_the_right_pull_request(self):
        _, mock_run = await self._run(_payload([]), target_repo="acme/widgets", pr_number=99)

        argv = mock_run.call_args[0][0]
        self.assertIn("-F", argv)
        self.assertIn("owner=acme", argv)
        self.assertIn("repo=widgets", argv)
        self.assertIn("number=99", argv)

    async def test_a_failed_call_returns_nothing_rather_than_raising(self):
        """A GraphQL hiccup must not fail the tick; it just means no inline comments."""
        result, _ = await self._run("", code=1)
        self.assertEqual(result, [])

    async def test_unparseable_output_returns_nothing(self):
        result, _ = await self._run("not json at all")
        self.assertEqual(result, [])

    async def test_no_call_is_made_without_a_pr_number_or_repo(self):
        with patch("core.util.git_ops.run_cmd_async", AsyncMock()) as mock_run:
            self.assertEqual(
                await get_unresolved_review_threads("/ws", target_repo="org/repo", pr_number=None), []
            )
            self.assertEqual(
                await get_unresolved_review_threads("/ws", target_repo=None, pr_number=7), []
            )
            self.assertEqual(
                await get_unresolved_review_threads("/ws", target_repo="not-a-slug", pr_number=7), []
            )
        mock_run.assert_not_called()

    async def test_empty_comment_bodies_are_skipped(self):
        result, _ = await self._run(_payload([_thread("   ")]))
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
