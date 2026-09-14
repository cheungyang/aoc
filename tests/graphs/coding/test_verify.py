"""The gate between an implementation and a push.

Verification only re-runs when the tree has changed, a failure inside the
budget goes back to the worker with the digest cleared so it cannot skip, and a
missing command is a configuration halt rather than something to retry.
Retrying a config error spends the entire implement budget on a failure no
model can fix.
"""
from unittest.mock import AsyncMock, patch

from graphs.coding.nodes.implement import MAX_IMPLEMENT_ATTEMPTS
from graphs.coding.nodes.verify import verify_node
from tests.graphs.coding.fixtures import ManifestFixture, _task


class TestVerifyNode(ManifestFixture):
    async def test_same_tree_is_not_retested(self):
        task = _task(stage="verified", verified_digest="d1")
        self.write_manifest([task])

        with patch("graphs.coding.nodes.verify._run", AsyncMock()) as mock_run:
            result = await verify_node(self.base_state(task, impl_digest="d1"))

        mock_run.assert_not_called()
        self.assertTrue(result["test_run_passed"])

    async def test_missing_verification_command_is_a_config_halt(self):
        """No amount of LLM retries can add a command to the manifest (B4)."""
        task = _task(verification_command="")
        self.write_manifest([task])

        result = await verify_node(self.base_state(task, impl_digest="d1"))

        self.assertEqual(result["route"], "done")
        self.assertEqual(self.stored()["status"], "halted")
        self.assertEqual(self.stored()["last_error"]["kind"], "config")

    async def test_passing_tests_record_the_digest_they_passed_on(self):
        task = _task()
        self.write_manifest([task])

        with patch("graphs.coding.nodes.verify._run", AsyncMock(return_value=(0, "ok", ""))):
            result = await verify_node(self.base_state(task, impl_digest="d1"))

        self.assertTrue(result["test_run_passed"])
        self.assertEqual(self.stored()["verified_digest"], "d1")
        self.assertEqual(self.stored()["stage"], "verified")

    async def test_failing_tests_go_back_to_implement_within_budget(self):
        task = _task(attempts={"implement": 1})
        self.write_manifest([task])

        with patch("graphs.coding.nodes.verify._run", AsyncMock(return_value=(1, "", "boom"))):
            result = await verify_node(self.base_state(task, impl_digest="d1"))

        self.assertEqual(result["route"], "implement")
        self.assertIn("boom", result["test_stderr"])
        # The digest is cleared so implement cannot skip on the stale tree.
        self.assertIsNone(self.stored()["impl_digest"])

    async def test_failing_tests_halt_once_the_budget_is_gone(self):
        task = _task(attempts={"implement": MAX_IMPLEMENT_ATTEMPTS})
        self.write_manifest([task])

        with patch("graphs.coding.nodes.verify._run", AsyncMock(return_value=(1, "", "boom"))):
            result = await verify_node(self.base_state(task, impl_digest="d1"))

        self.assertEqual(result["route"], "done")
        self.assertEqual(self.stored()["status"], "halted")

    async def test_verify_does_not_require_a_pr(self):
        """Local tests must not be gated on a network operation (A3)."""
        task = _task()
        self.write_manifest([task])

        with patch("graphs.coding.nodes.verify._run", AsyncMock(return_value=(0, "ok", ""))):
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

        with patch("graphs.coding.nodes.verify._run", AsyncMock(return_value=(1, "", "1 test failed"))):
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
                   AsyncMock(return_value=(exit_code, "", stderr))):
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
