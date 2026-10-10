"""The gate between a pushed commit and a PR.

Verification only re-runs when the pushed commit has changed, a failure inside
the budget goes back to the worker with the digest cleared so it cannot skip, and a
missing command is a configuration halt rather than something to retry.
Retrying a config error spends the entire implement budget on a failure no
model can fix.
"""
from unittest.mock import AsyncMock, patch

from graphs.coding.nodes.implement import MAX_IMPLEMENT_ATTEMPTS
from graphs.coding.nodes.verify import verify_node
from graphs.coding.utils.sandbox import RunResult
from tests.graphs.coding.fixtures import ManifestFixture, _task as _base_task


def _task(**overrides):
    """A task as verify meets it: pushed, with the commit to test."""
    return _base_task(**{"stage": "pushed", "head_sha": "s1", **overrides})


class TestVerifyNode(ManifestFixture):
    async def test_same_commit_is_not_retested(self):
        task = _task(stage="verified", verified_sha="s1")
        self.write_manifest([task])

        with patch("graphs.coding.nodes.verify._run", AsyncMock()) as mock_run:
            result = await verify_node(self.base_state(task))

        mock_run.assert_not_called()
        self.assertTrue(result["test_run_passed"])

    async def test_a_new_commit_is_retested_even_at_the_verified_stage(self):
        task = _task(stage="verified", verified_sha="s0", head_sha="s1")
        self.write_manifest([task])

        with patch("graphs.coding.nodes.verify._run",
                   AsyncMock(return_value=RunResult(0, "ok", ""))) as mock_run:
            await verify_node(self.base_state(task))

        mock_run.assert_called_once()
        self.assertEqual(self.stored()["verified_sha"], "s1")

    async def test_nothing_pushed_goes_back_through_push(self):
        """Only a pushed commit can be verified: it is what a sandbox fetches."""
        task = _task(stage="implemented", head_sha=None)
        self.write_manifest([task])

        with patch("graphs.coding.nodes.verify._run", AsyncMock()) as mock_run:
            result = await verify_node(self.base_state(task))

        mock_run.assert_not_called()
        self.assertEqual(result["route"], "done")
        self.assertEqual(self.stored()["stage"], "implemented")
        self.assertIsNone(self.stored()["lease_owner"])

    async def test_a_missing_worktree_keeps_the_pushed_stage(self):
        """The code is on the remote; the next tick re-provisions from it."""
        import shutil

        shutil.rmtree(self.workspace)
        task = _task()
        self.write_manifest([task])

        result = await verify_node(self.base_state(task))

        self.assertEqual(result["route"], "done")
        self.assertEqual(self.stored()["stage"], "pushed")
        self.assertEqual(self.stored()["status"], "queued")

    async def test_the_runner_is_told_which_commit_it_is_testing(self):
        task = _task()
        self.write_manifest([task])

        with patch("graphs.coding.nodes.verify._run",
                   AsyncMock(return_value=RunResult(0, "ok", ""))) as mock_run:
            await verify_node(self.base_state(task))

        meta = mock_run.call_args.kwargs["meta"]
        self.assertEqual(meta["task_id"], "T1")
        self.assertEqual(meta["head_sha"], "s1")
        # A sandbox fetches the commit from here.
        self.assertEqual(meta["repo"], "org/repo")

    async def test_missing_verification_command_is_a_config_halt(self):
        """No amount of LLM retries can add a command to the manifest (B4)."""
        task = _task(verification_command="")
        self.write_manifest([task])

        result = await verify_node(self.base_state(task, impl_digest="d1"))

        self.assertEqual(result["route"], "done")
        self.assertEqual(self.stored()["status"], "halted")
        self.assertEqual(self.stored()["last_error"]["kind"], "config")

    async def test_passing_tests_record_the_commit_they_passed_on(self):
        task = _task()
        self.write_manifest([task])

        with patch("graphs.coding.nodes.verify._run", AsyncMock(return_value=RunResult(0, "ok", ""))):
            result = await verify_node(self.base_state(task, impl_digest="d1"))

        self.assertTrue(result["test_run_passed"])
        self.assertEqual(self.stored()["verified_sha"], "s1")
        self.assertEqual(self.stored()["stage"], "verified")

    async def test_failing_tests_go_back_to_implement_within_budget(self):
        task = _task(attempts={"implement": 1})
        self.write_manifest([task])

        with patch("graphs.coding.nodes.verify._run", AsyncMock(return_value=RunResult(1, "", "boom"))):
            result = await verify_node(self.base_state(task, impl_digest="d1"))

        self.assertEqual(result["route"], "implement")
        self.assertIn("boom", result["test_stderr"])
        # The digest is cleared so implement cannot skip on the stale tree.
        self.assertIsNone(self.stored()["impl_digest"])

    async def test_failing_tests_halt_once_the_budget_is_gone(self):
        task = _task(attempts={"implement": MAX_IMPLEMENT_ATTEMPTS})
        self.write_manifest([task])

        with patch("graphs.coding.nodes.verify._run", AsyncMock(return_value=RunResult(1, "", "boom"))):
            result = await verify_node(self.base_state(task, impl_digest="d1"))

        self.assertEqual(result["route"], "done")
        self.assertEqual(self.stored()["status"], "halted")

    async def test_verify_does_not_require_a_pr(self):
        """Local tests must not be gated on a network operation (A3)."""
        task = _task()
        self.write_manifest([task])

        with patch("graphs.coding.nodes.verify._run", AsyncMock(return_value=RunResult(0, "ok", ""))):
            result = await verify_node(self.base_state(task, impl_digest="d1", pr_url=""))

        self.assertTrue(result["test_run_passed"])

    async def test_the_attempt_count_travels_in_state_not_just_on_disk(self):
        """verify reads the budget off `current_task`.

        The scheduler takes that snapshot once, at the top of the tick. If the
        nodes leave it there, verify sees the opening attempt count forever and
        `budget_left` is always true — which is exactly how implement→verify ran
        25 times in a single tick before LangGraph's recursion limit stopped it.
        """
        task = _task(attempts={"implement": 1})
        self.write_manifest([task])

        with patch("graphs.coding.nodes.verify._run", AsyncMock(return_value=RunResult(1, "", "1 test failed"))):
            result = await verify_node(self.base_state(task, impl_digest="d1"))

        self.assertEqual(result["route"], "implement")
        # What implement will read next: the manifest's view, not the snapshot.
        self.assertEqual(result["current_task"]["attempts"]["implement"], 1)
        self.assertEqual(result["current_task"]["stage"], "provisioned")
        self.assertIsNone(result["current_task"]["impl_digest"])


class TestFailuresTheWorkerCannotFix(ManifestFixture):
    """A red run that says nothing about the code must not be handed to the coder.

    `npx vitest` in a worktree with no `node_modules` fails identically whatever
    the code says. Feeding that back to the worker had it rewrite a correct
    implementation 25 times, each attempt reading the dependency error as its
    own bug.
    """

    async def _verify_with(self, exit_code, stderr, task=None):
        task = task or _task()
        self.write_manifest([task])
        with patch("graphs.coding.nodes.verify._run",
                   AsyncMock(return_value=RunResult(exit_code, "", stderr))):
            return await verify_node(self.base_state(task, impl_digest="d1"))

    async def test_a_missing_dependency_halts_instead_of_spending_the_budget(self):
        result = await self._verify_with(1, "Error: Cannot find module 'react'")

        self.assertEqual(result["route"], "done")
        self.assertEqual(self.stored()["status"], "halted")
        self.assertEqual(self.stored()["last_error"]["kind"], "environment")

    async def test_a_halt_for_the_environment_keeps_the_implementation(self):
        """The code is not what failed, so re-running the worker would be waste."""
        await self._verify_with(1, "Cannot find module 'vitest'",
                                task=_task(stage="implemented", impl_digest="d1"))

        self.assertEqual(self.stored()["impl_digest"], "d1")

    async def test_a_command_that_is_not_installed_halts(self):
        result = await self._verify_with(127, "sh: npx: command not found")

        self.assertEqual(result["route"], "done")
        self.assertEqual(self.stored()["last_error"]["kind"], "environment")

    async def test_the_halt_message_names_the_real_problem(self):
        result = await self._verify_with(127, "sh: npx: command not found")

        self.assertIn("environment problem", result["error_message"])

    async def test_a_file_the_worker_forgot_to_write_is_still_its_problem(self):
        """A relative specifier is a missing file, not a missing dependency."""
        result = await self._verify_with(1, "Cannot find module './useFlashcards'")

        self.assertEqual(result["route"], "implement")
        self.assertEqual(self.stored()["last_error"]["kind"], "verification")

    async def test_an_ordinary_assertion_failure_still_goes_back_to_the_worker(self):
        result = await self._verify_with(1, "AssertionError: expected 3 to equal 4")

        self.assertEqual(result["route"], "implement")
        self.assertEqual(self.stored()["last_error"]["kind"], "verification")

    async def test_a_python_module_that_exists_in_the_worktree_is_a_code_error(self):
        import os

        os.makedirs(os.path.join(self.workspace, "app"), exist_ok=True)
        result = await self._verify_with(
            1, "ModuleNotFoundError: No module named 'app.handlers'"
        )

        self.assertEqual(result["route"], "implement")

    async def test_a_python_dependency_that_was_never_installed_halts(self):
        result = await self._verify_with(
            1, "ModuleNotFoundError: No module named 'httpx'"
        )

        self.assertEqual(result["route"], "done")
        self.assertEqual(self.stored()["last_error"]["kind"], "environment")


class TestVerifyBackend(ManifestFixture):
    """`_run` goes through the configured runner, not a hard-wired subprocess."""

    async def test_the_configured_runner_executes_the_verification_command(self):
        from graphs.coding.utils.sandbox import RunResult

        task = _task()
        self.write_manifest([task])
        runner = AsyncMock()
        runner.run = AsyncMock(return_value=RunResult(0, "ok", "", backend="fake"))

        with patch("graphs.coding.nodes.verify.get_runner", return_value=runner):
            result = await verify_node(self.base_state(task, impl_digest="d1"))

        self.assertTrue(result["test_run_passed"])
        steps = runner.run.call_args.args[0]
        self.assertEqual([s.name for s in steps], ["verify"])
        self.assertEqual(steps[0].command, task["verification_command"])
        self.assertEqual(runner.run.call_args.kwargs["workspace_path"], self.workspace)

    async def test_a_misconfigured_backend_halts_as_environment_not_verification(self):
        """A typo in the backend name must not cost the worker an attempt."""
        import os
        from graphs.coding.utils.sandbox import BACKEND_ENV_VAR

        task = _task()
        self.write_manifest([task])

        with patch.dict(os.environ, {BACKEND_ENV_VAR: "nope"}):
            result = await verify_node(self.base_state(task, impl_digest="d1"))

        self.assertEqual(result["route"], "done")
        self.assertEqual(self.stored()["last_error"]["kind"], "environment")
        self.assertIn("misconfigured", result["error_message"])

    def _runner(self, result, runs_setup):
        runner = AsyncMock()
        runner.runs_setup = runs_setup
        runner.run = AsyncMock(return_value=result)
        return runner

    async def test_a_remote_runner_runs_the_projects_setup_first(self):
        """The sandbox starts empty, so it installs before it tests."""
        task = _task()
        self.write_manifest([task], setup_command="npm ci", environment={"backend": "e2b"})
        runner = self._runner(RunResult(0, "ok", "", backend="e2b"), runs_setup=True)

        with patch("graphs.coding.nodes.verify.get_runner", return_value=runner) as factory:
            await verify_node(self.base_state(task))

        self.assertEqual(factory.call_args.args[0], {"backend": "e2b"})
        steps = runner.run.call_args.args[0]
        self.assertEqual([(s.name, s.command) for s in steps],
                         [("setup", "npm ci"), ("verify", "pytest -q")])

    async def test_the_local_runner_does_not_repeat_setup(self):
        """Locally the scheduler already ran setup in the worktree."""
        task = _task()
        self.write_manifest([task], setup_command="npm ci")
        runner = self._runner(RunResult(0, "ok", ""), runs_setup=False)

        with patch("graphs.coding.nodes.verify.get_runner", return_value=runner):
            await verify_node(self.base_state(task))

        self.assertEqual([s.name for s in runner.run.call_args.args[0]], ["verify"])


class TestRunnerFailures(ManifestFixture):
    """A sandbox that did not start says nothing about the code."""

    async def _verify_with(self, result, task=None):
        task = task or _task()
        self.write_manifest([task])
        with patch("graphs.coding.nodes.verify._run", AsyncMock(return_value=result)):
            return await verify_node(self.base_state(task, impl_digest="d1"))

    async def test_an_infra_failure_is_retried_later_without_spending_the_budget(self):
        result = await self._verify_with(
            RunResult(1, "", "boom", infra_error="sandbox did not start", backend="e2b"),
            task=_task(attempts={"implement": 1}),
        )

        self.assertEqual(result["route"], "done")
        self.assertEqual(result["error_message"], "")
        stored = self.stored()
        self.assertEqual(stored["stage"], "pushed")
        self.assertEqual(stored["status"], "queued")
        self.assertIsNone(stored["lease_owner"])
        self.assertEqual(stored["last_error"]["kind"], "infra")
        self.assertEqual(stored["attempts"], {"implement": 1, "infra": 1})

    async def test_repeated_infra_failures_halt_for_a_human(self):
        from graphs.coding.nodes.verify import MAX_INFRA_ATTEMPTS

        result = await self._verify_with(
            RunResult(1, "", "boom", infra_error="sandbox did not start", backend="e2b"),
            task=_task(attempts={"infra": MAX_INFRA_ATTEMPTS - 1}),
        )

        self.assertEqual(self.stored()["status"], "halted")
        self.assertIn("not tested", result["error_message"])

    async def test_a_pass_clears_the_infra_count(self):
        await self._verify_with(RunResult(0, "ok", ""),
                                task=_task(attempts={"implement": 1, "infra": 2}))

        self.assertEqual(self.stored()["attempts"], {"implement": 1})

    async def test_a_failed_setup_halts_as_environment(self):
        result = await self._verify_with(
            RunResult(1, "", "npm ERR! peer dep", failed_step="setup", backend="e2b"),
            task=_task(attempts={"implement": 1}),
        )

        self.assertEqual(result["route"], "done")
        stored = self.stored()
        self.assertEqual(stored["status"], "halted")
        self.assertEqual(stored["last_error"]["kind"], "environment")
        self.assertEqual(stored["attempts"], {"implement": 1})
        self.assertIn("setup_command", result["error_message"])
