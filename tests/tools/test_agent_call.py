"""Tests for the `agent_call` tool surface.

`agent_call` is now a wrapper: it validates the arguments an LLM supplied and
wraps the outcome in a tool-response envelope. The delegation mechanics it used
to own -- permissions, caller tagging, streaming, thread context -- live in
`core.agent.delegation` and are tested in `tests/core/agent/test_delegation.py`.
Re-testing them through the tool would only assert that the wrapper forwards.
"""
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.agent.delegation import DelegationResult
from core.util import format_tool_response
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


if __name__ == '__main__':
    unittest.main()
