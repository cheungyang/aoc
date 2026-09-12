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
