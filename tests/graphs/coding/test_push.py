"""The push that follows every implement.

Verification tests the commit on GitHub, and a tick on another host resumes
from `origin/<branch>`, so an attempt is not real until it is pushed. The push
must record the SHA it landed, never touch the default branch, retry a GitHub
outage within its own budget, and leave the implement budget alone.
"""
from unittest.mock import AsyncMock, patch

from graphs.coding.nodes.push import MAX_PUSH_ATTEMPTS, push_node
from tests.graphs.coding.fixtures import ManifestFixture, _task as _base_task


def _task(**overrides):
    """A task as push meets it: implemented, not yet pushed."""
    return _base_task(**{"stage": "implemented", "attempts": {"implement": 1}, **overrides})


class TestPushNode(ManifestFixture):
    def setUp(self):
        super().setUp()
        self.push = AsyncMock(return_value=(True, "pushed"))
        self.rev = AsyncMock(return_value="abc1234def")
        for p in (
            patch("graphs.coding.nodes.push.get_push_identity", return_value=None),
            patch("graphs.coding.nodes.push.git_ops.commit_and_push", self.push),
            patch("graphs.coding.nodes.push.git_ops.rev_parse", self.rev),
        ):
            p.start()
            self.addCleanup(p.stop)

    async def test_a_push_records_the_sha_and_routes_to_verify(self):
        task = _task()
        self.write_manifest([task])

        result = await push_node(self.base_state(task))

        self.assertEqual(result["route"], "verify")
        self.assertEqual(result["head_sha"], "abc1234def")
        self.assertEqual(result["current_task"]["head_sha"], "abc1234def")
        self.assertEqual(self.stored()["stage"], "pushed")
        self.assertEqual(self.stored()["head_sha"], "abc1234def")

    async def test_each_attempt_is_its_own_commit_on_the_task_branch(self):
        task = _task(attempts={"implement": 2})
        self.write_manifest([task])

        await push_node(self.base_state(task))

        kwargs = self.push.call_args.kwargs
        self.assertEqual(kwargs["branch_name"], "feat/demo/t1")
        self.assertIn("T1", kwargs["commit_msg"])
        self.assertIn("attempt 2", kwargs["commit_msg"])

    async def test_an_already_pushed_task_is_not_pushed_again(self):
        task = _task(stage="pushed", head_sha="s1")
        self.write_manifest([task])

        result = await push_node(self.base_state(task))

        self.push.assert_not_called()
        self.assertEqual(result["route"], "verify")
        self.assertEqual(result["head_sha"], "s1")

    async def test_it_refuses_to_push_to_the_default_branch(self):
        """Attempts may land on the remote only because a task branch contains them."""
        task = _task()
        self.write_manifest([task])

        result = await push_node(self.base_state(task, branch_name="main"))

        self.push.assert_not_called()
        self.assertEqual(result["route"], "done")
        self.assertEqual(self.stored()["status"], "halted")
        self.assertEqual(self.stored()["last_error"]["kind"], "config")

    async def test_a_missing_worktree_yields_without_pushing(self):
        import shutil

        shutil.rmtree(self.workspace)
        task = _task()
        self.write_manifest([task])

        result = await push_node(self.base_state(task))

        self.push.assert_not_called()
        self.assertEqual(result["route"], "done")
        self.assertIsNone(self.stored()["lease_owner"])

    async def test_a_transient_failure_retries_at_push(self):
        self.push.return_value = (False, "network down")
        task = _task()
        self.write_manifest([task])

        result = await push_node(self.base_state(task))

        stored = self.stored()
        self.assertEqual(result["route"], "done")
        self.assertEqual(stored["status"], "queued")
        self.assertIsNone(stored["lease_owner"])
        # Stage kept, so the next tick re-enters here instead of re-implementing.
        self.assertEqual(stored["stage"], "implemented")
        self.assertEqual(stored["attempts"]["push"], 1)
        self.assertEqual(stored["attempts"]["implement"], 1)
        self.assertEqual(stored["last_error"]["kind"], "git")

    async def test_an_unresolvable_head_counts_as_a_failed_push(self):
        self.rev.return_value = ""
        task = _task()
        self.write_manifest([task])

        result = await push_node(self.base_state(task))

        self.assertEqual(result["route"], "done")
        self.assertEqual(self.stored()["stage"], "implemented")
        self.assertEqual(self.stored()["attempts"]["push"], 1)

    async def test_push_halts_once_its_own_budget_is_gone(self):
        self.push.return_value = (False, "network down")
        task = _task(attempts={"implement": 1, "push": MAX_PUSH_ATTEMPTS - 1})
        self.write_manifest([task])

        await push_node(self.base_state(task))

        self.assertEqual(self.stored()["status"], "halted")
        self.assertIsNone(self.stored()["lease_owner"])
