"""Tests for the `agent_call` tool surface.

`agent_call` is now a wrapper: it validates the arguments an LLM supplied and
wraps the outcome in a tool-response envelope. The delegation mechanics it used
to own -- permissions, caller tagging, streaming, thread context -- live in
`core.runtime.delegation` and are tested in `tests/core/runtime/test_delegation.py`.
Re-testing them through the tool would only assert that the wrapper forwards.
"""
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.permissions import access
from core.runtime.delegation import DelegationResult
from core.runtime.foundation.context import current_execution_context
from core.runtime.foundation.sessions import SessionManager
from core.tooling import format_tool_response
from tests.household import ConfigLoader, dormant, household
from tools.agent_call import agent_call


class TestAgentCallTool(unittest.IsolatedAsyncioTestCase):

    @patch('tools.agent_call.stream_delegate')
    async def test_forwards_arguments_and_wraps_payload(self, mock_delegate):
        mock_delegate.return_value = DelegationResult(agent_id="agent1", text="agent response")

        result = await agent_call.ainvoke({
            "agent_id": "agent1",
            "prompt": "hello",
            "channel": "software-dev",
            "caller": "main",
        })

        mock_delegate.assert_awaited_once_with(
            agent_id="agent1",
            prompt="hello",
            channel="software-dev",
            caller="main",
            run_async=False,
        )
        self.assertEqual(
            result,
            format_tool_response("agent_call", payload="agent response", errors="None"),
        )

    @patch('tools.agent_call.stream_delegate')
    async def test_run_async_is_forwarded(self, mock_delegate):
        mock_delegate.return_value = DelegationResult(
            agent_id="agent1", text="Successfully triggered agent 'agent1'.", job_id="job_123"
        )

        result = await agent_call.ainvoke({
            "agent_id": "agent1",
            "prompt": "hello",
            "channel": "software-dev",
            "run_async": True,
        })

        self.assertTrue(mock_delegate.await_args.kwargs["run_async"])
        self.assertIn("Successfully triggered agent 'agent1'", result)

    @patch('tools.agent_call.stream_delegate')
    async def test_delegation_error_becomes_an_error_envelope(self, mock_delegate):
        """A refused delegation is reported to the model as a tool error, not raised."""
        mock_delegate.return_value = DelegationResult(
            agent_id="scott",
            error="Agent 'scott' cannot be called in channel 'general'. Allowed channels: ['software-dev']",
        )

        result = await agent_call.ainvoke({
            "agent_id": "scott",
            "prompt": "hello",
            "channel": "general",
        })

        self.assertIn("cannot be called in channel 'general'", result)
        self.assertIn("Allowed channels: ['software-dev']", result)

    @patch('tools.agent_call.stream_delegate', new_callable=AsyncMock)
    async def test_unexpected_exception_is_contained(self, mock_delegate):
        mock_delegate.side_effect = RuntimeError("boom")

        result = await agent_call.ainvoke({
            "agent_id": "agent1",
            "prompt": "hello",
            "channel": "software-dev",
        })

        self.assertIn("Error calling agent: boom", result)

    async def test_empty_required_args_hit_the_tools_own_guard(self):
        """Passing a *missing* key only exercises pydantic's schema validation, so
        the tool's own guard is never reached that way. Empty values get past
        pydantic and into the tool."""
        for missing, args in (
            ("agent_id", {"agent_id": "", "prompt": "hello", "channel": "general"}),
            ("prompt", {"agent_id": "agent1", "prompt": "", "channel": "general"}),
            ("channel", {"agent_id": "agent1", "prompt": "hello", "channel": ""}),
        ):
            with self.subTest(missing=missing):
                result = await agent_call.ainvoke(args)
                self.assertIn(
                    "Error: agent_call requires 'agent_id', 'prompt', and 'channel'.",
                    result
                )


@patch('tools.agent_call.AgentsLoader', ConfigLoader)
@patch('tools.agent_call.stream_delegate', new_callable=AsyncMock)
class TestWhoTheCallIsFor(unittest.IsolatedAsyncioTestCase):
    """An agent another agent's model asks for works for whoever the turn is for, so it
    takes `use` on it: player2 may use Ramsay and Eric, and Daisy isn't theirs."""

    def setUp(self):
        patcher = household()
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    async def _call(agent_id, person=None):
        """agent_call asked in a turn for `person`; None: a turn nobody started."""
        token = None
        if person is not None:
            ctx = SessionManager.get_session(agent_id="main", source="discord", channel="general", person=person)
            token = current_execution_context.set(ctx)
        try:
            return await agent_call.ainvoke({"agent_id": agent_id, "prompt": "plan Saturday", "channel": "general"})
        finally:
            if token is not None:
                current_execution_context.reset(token)

    async def test_an_agent_the_person_may_not_use_is_refused(self, mock_delegate):
        result = await self._call("day-planner", person="player2")
        self.assertIn("Error: Daisy ('day-planner') isn't open to player2, who this turn is for", result)
        mock_delegate.assert_not_awaited()

    async def test_ones_they_may_use_are_called(self, mock_delegate):
        for agent_id in ("meal-planner", "excursion-planner"):
            with self.subTest(agent=agent_id):
                mock_delegate.reset_mock()
                mock_delegate.return_value = DelegationResult(agent_id=agent_id, text="Tacos on Saturday.")
                result = await self._call(agent_id, person="player2")
                mock_delegate.assert_awaited_once()
                self.assertEqual(result, format_tool_response("agent_call", payload="Tacos on Saturday.",
                                                              errors="None"))

    async def test_the_owner_may_call_any_of_his(self, mock_delegate):
        mock_delegate.return_value = DelegationResult(agent_id="day-planner", text="Free after 2.")
        self.assertIn("Free after 2.", await self._call("day-planner", person="owner"))

    async def test_a_turn_nobody_started_is_never_refused(self, mock_delegate):
        mock_delegate.return_value = DelegationResult(agent_id="day-planner", text="Free after 2.")
        self.assertIn("Free after 2.", await self._call("day-planner"))

    async def test_an_agent_nobody_knows_is_left_to_the_delegation_to_report(self, mock_delegate):
        mock_delegate.return_value = DelegationResult(agent_id="ghost", error="Agent 'ghost' not found.")
        self.assertIn("Agent 'ghost' not found.", await self._call("ghost", person="player2"))

    async def test_with_nobody_configured_the_owner_calls_every_agent_without_a_block(self, mock_delegate):
        mock_delegate.return_value = DelegationResult(agent_id="day-planner", text="Free after 2.")
        with dormant():
            self.assertIn("Free after 2.", await self._call("day-planner", person=access.person_for("anyone")))


if __name__ == '__main__':
    unittest.main()
