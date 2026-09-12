"""The optional review pass in front of publish.

An advisory audit annotates and a blocking audit sends the work back, and an
unreachable model is neither. Treating an outage as a rejection would bounce
correct work back to the worker — and spend an implement attempt doing it —
every time the provider hiccups.
"""
from unittest.mock import AsyncMock, patch

from graphs.coding.nodes.audit import audit_node, resolve_audit_mode
from tests.graphs.coding.fixtures import ManifestFixture, _task


class TestAuditNode(ManifestFixture):
    def test_unknown_mode_falls_back_to_advisory(self):
        self.assertEqual(resolve_audit_mode({"audit_mode": "nonsense"}), "advisory")
        self.assertEqual(resolve_audit_mode({}), "advisory")
        self.assertEqual(resolve_audit_mode({"audit_mode": "BLOCKING"}), "blocking")

    async def test_off_skips_the_call_entirely(self):
        task = _task(stage="verified")
        self.write_manifest([task])

        with patch("graphs.coding.nodes.audit._run_audit", AsyncMock()) as mock_audit:
            result = await audit_node(self.base_state(task, audit_mode="off"))

        mock_audit.assert_not_called()
        self.assertEqual(result["stage"], "audited")

    async def test_advisory_rejection_still_publishes(self):
        task = _task(stage="verified")
        self.write_manifest([task])
        verdict = {"passed": False, "feedback": "magic numbers"}

        with patch("graphs.coding.nodes.audit.git_ops.get_git_diff", AsyncMock(return_value="diff")), \
             patch("graphs.coding.nodes.audit._run_audit", AsyncMock(return_value=verdict)):
            result = await audit_node(self.base_state(task, audit_mode="advisory"))

        self.assertNotEqual(result.get("route"), "implement")
        self.assertEqual(result["audit_feedback"], "magic numbers")

    async def test_blocking_rejection_returns_to_implement(self):
        task = _task(stage="verified")
        self.write_manifest([task])
        verdict = {"passed": False, "feedback": "fake implementation"}

        with patch("graphs.coding.nodes.audit.git_ops.get_git_diff", AsyncMock(return_value="diff")), \
             patch("graphs.coding.nodes.audit._run_audit", AsyncMock(return_value=verdict)):
            result = await audit_node(self.base_state(task, audit_mode="blocking"))

        self.assertEqual(result["route"], "implement")
        self.assertEqual(self.stored()["stage"], "provisioned")

    async def test_an_llm_outage_is_not_a_rejection(self):
        from graphs.coding.nodes.audit import _run_audit

        with patch("tools.agent_call.agent_call") as mock_agent:
            mock_agent.ainvoke = AsyncMock(side_effect=RuntimeError("model down"))
            verdict = await _run_audit(spec_content="spec", diff="d", channel="c")

        self.assertTrue(verdict["passed"])
        self.assertEqual(verdict["feedback"], "")
