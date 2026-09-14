"""Task selection and the lease that guards it.

The scheduler decides what a tick is allowed to touch. Reviews are picked
before new work because a merge can unblock a dependent inside the same tick, a
resumed task re-enters at the stage it reached rather than at the worker, and
the concurrency bound holds across processes because it counts live leases
instead of anything held in memory. Get this wrong and two ticks implement the
same task, or one crashed tick blocks the queue until its lease ages out.
"""
import os
import time
import unittest
from unittest.mock import AsyncMock, patch

from graphs.coding.nodes.scheduler import (
    ROUTE_DONE,
    ROUTE_IMPLEMENT,
    ROUTE_PUBLISH,
    ROUTE_SYNC,
    scheduler_node,
    select_task,
)
from graphs.coding.utils import manifest as manifest_store
from tests.graphs.coding.fixtures import ManifestFixture, _task


class TestSelectTask(unittest.TestCase):
    def test_reviews_are_picked_before_new_work(self):
        """A merge can unblock a dependent inside the same tick, so sync first."""
        queue = [_task(task_id="NEW"), _task(task_id="REV", status="awaiting_review")]
        task, route = select_task(queue, handled=[], now=100.0)
        self.assertEqual(task["task_id"], "REV")
        self.assertEqual(route, ROUTE_SYNC)

    def test_review_outside_its_poll_window_is_dormant(self):
        queue = [_task(task_id="REV", status="awaiting_review", poll_until=50.0)]
        task, route = select_task(queue, handled=[], now=100.0)
        self.assertIsNone(task)
        self.assertEqual(route, ROUTE_DONE)

    def test_review_inside_its_poll_window_is_synced(self):
        queue = [_task(task_id="REV", status="awaiting_review", poll_until=500.0)]
        task, route = select_task(queue, handled=[], now=100.0)
        self.assertEqual(task["task_id"], "REV")
        self.assertEqual(route, ROUTE_SYNC)

    def test_leased_review_is_left_to_its_owner(self):
        queue = [_task(task_id="REV", status="awaiting_review",
                       lease_owner="other", lease_expires_at=999.0)]
        task, _ = select_task(queue, handled=[], now=100.0)
        self.assertIsNone(task)

    def test_already_handled_task_is_not_picked_twice(self):
        queue = [_task(task_id="T1")]
        task, route = select_task(queue, handled=["T1"], now=100.0)
        self.assertIsNone(task)
        self.assertEqual(route, ROUTE_DONE)

    def test_verified_task_resumes_at_publish_not_at_the_llm(self):
        """The whole point of the stage machine: a crash after verify costs no tokens."""
        queue = [_task(task_id="T1", stage="verified")]
        task, route = select_task(queue, handled=[], now=100.0)
        self.assertEqual(route, ROUTE_PUBLISH)

    def test_published_task_resumes_at_sync(self):
        queue = [_task(task_id="T1", stage="published")]
        _, route = select_task(queue, handled=[], now=100.0)
        self.assertEqual(route, ROUTE_SYNC)

    def test_fresh_task_starts_at_implement(self):
        queue = [_task(task_id="T1", stage="queued")]
        _, route = select_task(queue, handled=[], now=100.0)
        self.assertEqual(route, ROUTE_IMPLEMENT)


class TestSchedulerNode(ManifestFixture):
    def setUp(self):
        super().setUp()
        self.preflight = patch(
            "graphs.coding.nodes.scheduler.preflight_tick",
            AsyncMock(return_value=(True, "", None))
        )
        self.mock_preflight = self.preflight.start()
        self.addCleanup(self.preflight.stop)
        self.provision = patch(
            "graphs.coding.nodes.scheduler.git_ops.provision_worktree",
            AsyncMock(return_value=(True, "provisioned"))
        )
        self.mock_provision = self.provision.start()
        self.addCleanup(self.provision.stop)

    def tick_state(self, **overrides):
        state = {"build_request_path": self.manifest_path, "tick_report": [], "tick_handled": []}
        state.update(overrides)
        return state

    async def test_a_tick_with_no_project_named_says_so_and_stops(self):
        """Which project is an input, not a default. Reported, not raised: the
        caller needs the sentence that tells them what to pass."""
        result = await scheduler_node(
            {"build_request_path": "", "tick_report": [], "tick_handled": []}
        )

        self.assertEqual(result["route"], ROUTE_DONE)
        self.assertIn("build_request.json", result["error_message"])
        self.mock_preflight.assert_not_called()

    async def test_an_empty_queue_reports_nothing(self):
        """A quiet tick must produce no output at all, or the channel fills up."""
        self.write_manifest([])
        result = await scheduler_node(self.tick_state())

        self.assertEqual(result["route"], ROUTE_DONE)
        self.assertEqual(result["tick_report"], [])

    async def test_an_empty_queue_costs_no_network_call(self):
        self.write_manifest([_task(status="done", stage="done")])
        await scheduler_node(self.tick_state())
        self.mock_preflight.assert_not_called()

    async def test_a_dead_run_is_reclaimed(self):
        """The fix for 'stuck in_progress forever': the lease, not the status, decides."""
        self.write_manifest([_task(
            status="active", stage="provisioned",
            lease_owner="dead_tick", lease_expires_at=time.time() - 10
        )])

        result = await scheduler_node(self.tick_state())

        self.assertIn("Reclaimed", "\n".join(result["tick_report"]))
        self.assertEqual(result["route"], ROUTE_IMPLEMENT)

    async def test_a_failed_preflight_stops_before_any_token_is_spent(self):
        self.write_manifest([_task()])
        self.mock_preflight.return_value = (False, "Preflight failed: bot token expired", None)

        result = await scheduler_node(self.tick_state())

        self.assertEqual(result["route"], ROUTE_DONE)
        self.assertIn("bot token expired", result["error_message"])
        self.mock_provision.assert_not_called()

    async def test_claiming_a_task_writes_a_lease(self):
        self.write_manifest([_task()])
        result = await scheduler_node(self.tick_state(lease_owner="tick_abc"))

        stored = self.stored()
        self.assertEqual(result["route"], ROUTE_IMPLEMENT)
        self.assertEqual(stored["lease_owner"], "tick_abc")
        self.assertTrue(manifest_store.lease_is_active(stored))
        self.assertEqual(stored["status"], "active")

    async def test_a_task_claimed_elsewhere_is_skipped(self):
        self.write_manifest([_task(lease_owner="other", lease_expires_at=time.time() + 600)])
        # A live lease also hides the task from the runnable set, so the tick is a no-op.
        result = await scheduler_node(self.tick_state(lease_owner="tick_abc"))
        self.assertEqual(result["route"], ROUTE_DONE)

    async def test_a_review_is_not_reprovisioned(self):
        """Re-creating the worktree under a task awaiting review throws the code away."""
        self.write_manifest([_task(
            status="awaiting_review", stage="awaiting_review",
            pr_url="https://github.com/org/repo/pull/7"
        )])

        result = await scheduler_node(self.tick_state())

        self.assertEqual(result["route"], ROUTE_SYNC)
        self.mock_provision.assert_not_called()

    async def test_a_failed_provision_halts_with_the_git_error(self):
        self.write_manifest([_task()])
        self.mock_provision.return_value = (False, "fatal: invalid reference origin/main")

        result = await scheduler_node(self.tick_state())

        self.assertEqual(result["route"], ROUTE_DONE)
        self.assertEqual(self.stored()["status"], "halted")
        self.assertEqual(self.stored()["last_error"]["kind"], "git")
        self.assertIsNone(self.stored()["lease_owner"])

    async def test_a_verified_task_is_not_reprovisioned_into_a_clean_tree(self):
        task = _task(stage="verified", run_id="run_X", branch_name="feat/demo/t1")
        self.write_manifest([task])
        os.makedirs(os.path.join("workspaces", "runs", "run_X"), exist_ok=True)
        self.addCleanup(lambda: os.rmdir(os.path.join("workspaces", "runs", "run_X")))

        result = await scheduler_node(self.tick_state())

        self.assertEqual(result["route"], ROUTE_PUBLISH)
        self.mock_provision.assert_not_called()

    async def test_the_tick_idles_when_the_concurrency_bound_is_already_met(self):
        """Counting live leases is what makes the bound hold across processes.

        A per-process counter would let two ticks each believe they were the
        only one running.
        """
        self.write_manifest([
            _task(task_id="busy", status="active",
                  lease_owner="other_tick", lease_expires_at=time.time() + 600),
            _task(task_id="waiting"),
        ], max_concurrency=1)

        result = await scheduler_node(self.tick_state())

        self.assertEqual(result["route"], ROUTE_DONE)
        self.assertIsNone(result["current_task"])
        self.mock_provision.assert_not_called()

    async def test_an_expired_lease_does_not_count_against_the_bound(self):
        # Otherwise one crashed tick would block the queue until its lease aged
        # out, which is exactly the stall the reclaim exists to prevent.
        self.write_manifest([
            _task(task_id="dead", status="active",
                  lease_owner="crashed", lease_expires_at=time.time() - 1),
            _task(task_id="waiting"),
        ], max_concurrency=1)

        result = await scheduler_node(self.tick_state())

        self.assertIsNotNone(result["current_task"])

    async def test_a_higher_bound_lets_a_second_task_start(self):
        self.write_manifest([
            _task(task_id="busy", status="active",
                  lease_owner="other_tick", lease_expires_at=time.time() + 600),
            _task(task_id="waiting"),
        ], max_concurrency=2)

        result = await scheduler_node(self.tick_state())

        self.assertEqual(result["current_task"]["task_id"], "waiting")

    async def test_this_tick_s_own_lease_does_not_block_it(self):
        # A tick that claimed a task earlier in the same run must still be able
        # to continue; only *other* owners count as in-flight.
        self.write_manifest([
            _task(task_id="mine", status="active",
                  lease_owner="tick_me", lease_expires_at=time.time() + 600),
            _task(task_id="waiting"),
        ], max_concurrency=1)

        result = await scheduler_node(self.tick_state(lease_owner="tick_me"))

        self.assertEqual(result["current_task"]["task_id"], "waiting")

    async def test_the_worktree_is_provisioned_from_the_resolved_repo_root(self):
        self.write_manifest([_task()])

        with patch("graphs.coding.nodes.scheduler.ensure_repo_available",
                   AsyncMock(return_value=("/tmp/managed_clone", ""))):
            result = await scheduler_node(self.tick_state())

        self.assertEqual(result["repo_root"], "/tmp/managed_clone")
        self.assertEqual(
            self.mock_provision.await_args.kwargs["repo_path"], "/tmp/managed_clone"
        )
        self.assertTrue(
            result["workspace_path"].startswith("/tmp/managed_clone"),
            f"worktree escaped the repo root: {result['workspace_path']}"
        )

    async def test_a_repo_that_cannot_be_reached_idles_instead_of_provisioning(self):
        self.write_manifest([_task()])

        with patch("graphs.coding.nodes.scheduler.ensure_repo_available",
                   AsyncMock(return_value=("", "Could not clone `owner/repo`: denied"))):
            result = await scheduler_node(self.tick_state())

        self.assertEqual(result["route"], ROUTE_DONE)
        self.mock_provision.assert_not_called()
        self.assertIn("denied", " ".join(result["tick_report"]))

    async def test_a_repo_failure_does_not_leave_the_task_claimed(self):
        # The lease is taken after the repo is resolved, so a failure here must
        # leave the task exactly as it found it.
        self.write_manifest([_task()])

        with patch("graphs.coding.nodes.scheduler.ensure_repo_available",
                   AsyncMock(return_value=("", "no network"))):
            await scheduler_node(self.tick_state())

        self.assertIsNone(self.stored().get("lease_owner"))


class TestSetupCommand(TestSchedulerNode):
    """Scaffolding and dependency install belong to the worktree, not to the
    test command: run once, and a failure is the environment's fault."""

    def setUp(self):
        super().setUp()
        self.run = patch(
            "graphs.coding.nodes.scheduler.run_in_worktree",
            AsyncMock(return_value=(0, "added 312 packages", ""))
        )
        self.mock_run = self.run.start()
        self.addCleanup(self.run.stop)

    async def test_a_fresh_worktree_is_set_up_before_any_code_is_written(self):
        self.write_manifest([_task()], setup_command="npm install")

        result = await scheduler_node(self.tick_state())

        self.mock_run.assert_awaited_once()
        self.assertEqual(self.mock_run.await_args.args[0], "npm install")
        self.assertEqual(result["route"], ROUTE_IMPLEMENT)
        self.assertTrue(self.stored()["setup_done"])

    async def test_setup_runs_in_the_worktree_not_the_repo_root(self):
        self.write_manifest([_task()], setup_command="npm install")

        result = await scheduler_node(self.tick_state())

        self.assertEqual(
            self.mock_run.await_args.kwargs["cwd"], result["workspace_path"]
        )

    async def test_a_task_level_command_overrides_the_manifest_one(self):
        self.write_manifest(
            [_task(setup_command="pip install -e .")], setup_command="npm install"
        )

        await scheduler_node(self.tick_state())

        self.assertEqual(self.mock_run.await_args.args[0], "pip install -e .")

    async def test_setup_is_not_repeated_once_it_has_succeeded(self):
        """Re-installing on every tick is the cost this field exists to remove."""
        os.makedirs(os.path.join(self.workspace, "runs"), exist_ok=True)
        self.write_manifest(
            [_task(stage="provisioned", setup_done=True)], setup_command="npm install"
        )

        with patch("os.path.exists", return_value=True):
            await scheduler_node(self.tick_state())

        self.mock_run.assert_not_awaited()

    async def test_re_provisioning_makes_setup_run_again(self):
        """A recreated worktree has no `node_modules`, so a stale flag would
        send the worker into a directory that cannot build."""
        self.write_manifest(
            [_task(stage="provisioned", setup_done=True)], setup_command="npm install"
        )

        # The worktree path does not exist, so the scheduler re-provisions.
        await scheduler_node(self.tick_state())

        self.mock_run.assert_awaited_once()
        self.assertTrue(self.stored()["setup_done"])

    async def test_a_failed_setup_halts_the_task_as_an_environment_fault(self):
        self.write_manifest([_task()], setup_command="npm install")
        self.mock_run.return_value = (1, "", "npm ERR! code ENOTFOUND")

        result = await scheduler_node(self.tick_state())

        stored = self.stored()
        self.assertEqual(result["route"], ROUTE_DONE)
        self.assertEqual(stored["status"], "halted")
        self.assertEqual(stored["last_error"]["kind"], "environment")
        self.assertIn("ENOTFOUND", stored["last_error"]["message"])

    async def test_a_failed_setup_does_not_spend_the_implement_budget(self):
        """The whole point of the split: the worker never saw this task, so it
        must not be charged for it."""
        self.write_manifest([_task()], setup_command="npm install")
        self.mock_run.return_value = (127, "", "sh: npm: command not found")

        await scheduler_node(self.tick_state())

        self.assertEqual(self.stored().get("attempts", {}).get("implement", 0), 0)

    async def test_a_failed_setup_releases_the_lease(self):
        self.write_manifest([_task()], setup_command="npm install")
        self.mock_run.return_value = (1, "", "boom")

        await scheduler_node(self.tick_state())

        self.assertIsNone(self.stored().get("lease_owner"))

    async def test_no_setup_command_means_no_shell_at_all(self):
        self.write_manifest([_task()])

        result = await scheduler_node(self.tick_state())

        self.mock_run.assert_not_awaited()
        self.assertEqual(result["route"], ROUTE_IMPLEMENT)


if __name__ == "__main__":
    unittest.main()
