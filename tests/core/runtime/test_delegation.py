"""Tests for the shared delegation path.

These moved here wholesale from `tests/tools/test_agent_call.py`. The behaviours
they cover -- channel permissions, caller tagging, thread context, stateless
dispatch, streamed events -- stopped being the tool's behaviours when the tool
became a thin wrapper, and a test that patches `tools.agent_call.AgentsLoader`
would now be asserting against a symbol that isn't there.
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import ANY, AsyncMock, MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.runtime.delegation import (
    agent_header,
    channel_permits,
    prompt_to_text,
    stream_delegate,
    with_caller,
)
from core.runtime.stream_handler import (
    ROUTE_REACTION,
    SUBAGENT_STREAM_FINAL,
    SUBAGENT_STREAM_TOKEN,
)


def _streaming_agent(config, text="agent response", capture=None):
    """An agent double whose stream yields `text` as two tokens then a final."""
    agent = MagicMock()
    agent.config = config

    async def fake_stream(prompt, session=None):
        if capture is not None:
            capture["prompt"] = prompt
            capture["session"] = session
        yield {"type": "token", "content": text}
        yield {"type": "final_response", "text": text, "response": MagicMock(text=text)}

    agent.execute_stream = fake_stream
    return agent


class TestDelegationHelpers(unittest.TestCase):
    """The pure functions, which is where the multimodal regression would hide."""

    def test_with_caller_prefixes_a_string(self):
        self.assertEqual(with_caller("hello", "main"), "<caller>main</caller>\nhello")

    def test_with_caller_does_not_duplicate(self):
        original = "<caller>custom</caller>\nhello"
        self.assertEqual(with_caller(original, "main"), original)

    def test_with_caller_without_caller_is_identity(self):
        self.assertEqual(with_caller("hello", None), "hello")

    def test_with_caller_preserves_multimodal_parts(self):
        """The image part has to survive. Stringifying the payload to attach the
        caller tag is exactly how a receipt photo became a paraphrase."""
        payload = [
            {"type": "text", "text": "what is this?"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
        ]
        result = with_caller(payload, "main")

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], {"type": "text", "text": "<caller>main</caller>\n"})
        self.assertEqual(result[1:], payload)

    def test_with_caller_does_not_duplicate_in_multimodal(self):
        payload = [{"type": "text", "text": "<caller>custom</caller>\nhi"}]
        self.assertEqual(with_caller(payload, "main"), payload)

    def test_prompt_to_text_flattens_parts(self):
        payload = [
            {"type": "text", "text": "look at "},
            {"type": "image_url", "image_url": {"url": "data:..."}},
        ]
        self.assertEqual(prompt_to_text(payload), "look at [image]")

    def test_channel_permits_wildcard_and_case(self):
        self.assertTrue(channel_permits({"channels": ["*"]}, "anything"))
        self.assertTrue(channel_permits({"channels": ["Software-Dev"]}, "software-dev"))
        self.assertFalse(channel_permits({"channels": ["software-dev"]}, "general"))
        self.assertFalse(channel_permits({}, "general"))

    def test_agent_header_falls_back(self):
        self.assertEqual(agent_header({"emoji": "🐘", "name": "Elephant"}, "sp"), "🐘 Elephant: ")
        self.assertEqual(agent_header({}, "sp"), "🤖 sp: ")


class TestStreamDelegate(unittest.IsolatedAsyncioTestCase):

    @patch('core.channel.discord.loader.BotsLoader')
    @patch('core.loaders.agents_loader.AgentsLoader')
    async def test_success_returns_text(self, mock_agents_loader, mock_bots_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.get_agent.return_value = _streaming_agent(
            {"channels": ["software-dev"], "emoji": "💻", "name": "Software Developer"}
        )
        mock_bots_loader.return_value.find_channel.return_value = MagicMock()

        result = await stream_delegate("agent1", "hello", "software-dev")

        mock_loader.get_agent.assert_called_once_with("agent1")
        mock_bots_loader.return_value.find_channel.assert_called_once_with("software-dev")
        self.assertTrue(result.ok)
        self.assertEqual(result.text, "agent response")
        self.assertEqual(result.agent_id, "agent1")

    @patch('core.channel.discord.loader.BotsLoader')
    @patch('core.loaders.agents_loader.AgentsLoader')
    async def test_wildcard_channel_allowed(self, mock_agents_loader, mock_bots_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.get_agent.return_value = _streaming_agent({"channels": ["*"]})
        mock_bots_loader.return_value.find_channel.return_value = MagicMock()

        result = await stream_delegate("main", "hello", "any-channel")

        self.assertTrue(result.ok)
        self.assertEqual(result.text, "agent response")

    @patch('core.loaders.agents_loader.AgentsLoader')
    async def test_channel_restriction_is_an_error_not_an_exception(self, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        agent = MagicMock()
        agent.config = {"channels": ["software-dev"]}
        mock_loader.get_agent.return_value = agent

        result = await stream_delegate("scott", "hello", "general")

        self.assertFalse(result.ok)
        self.assertIn("cannot be called in channel 'general'", result.error)
        self.assertIn("Allowed channels: ['software-dev']", result.error)

    async def test_missing_arguments_are_rejected(self):
        for label, args in (
            ("agent_id", ("", "hello", "general")),
            ("prompt", ("agent1", None, "general")),
            ("channel", ("agent1", "hello", "")),
        ):
            with self.subTest(missing=label):
                result = await stream_delegate(*args)
                self.assertFalse(result.ok)
                self.assertIn("requires 'agent_id', 'prompt', and 'channel'", result.error)

    @patch('core.channel.discord.loader.BotsLoader')
    @patch('core.loaders.agents_loader.AgentsLoader')
    async def test_run_async_starts_a_background_task(self, mock_agents_loader, mock_bots_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        agent = MagicMock()
        agent.config = {"channels": ["software-dev"]}
        agent.execute = AsyncMock(return_value="agent response")
        mock_loader.get_agent.return_value = agent
        mock_bots_loader.return_value.find_channel.return_value = MagicMock()

        result = await stream_delegate("agent1", "hello", "software-dev", run_async=True)

        await asyncio.sleep(0.1)
        agent.execute.assert_called_once_with("hello", session=ANY)
        self.assertTrue(result.ok)
        self.assertIn("Successfully triggered agent 'agent1'", result.text)
        self.assertIsNotNone(result.job_id)

    @patch('core.channel.discord.loader.BotsLoader')
    @patch('core.loaders.agents_loader.AgentsLoader')
    async def test_explicit_caller_is_tagged(self, mock_agents_loader, mock_bots_loader):
        capture = {}
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.get_agent.return_value = _streaming_agent(
            {"channels": ["software-dev"]}, capture=capture
        )
        mock_bots_loader.return_value.find_channel.return_value = MagicMock()

        await stream_delegate("agent1", "hello", "software-dev", caller="main")

        self.assertEqual(capture["prompt"], "<caller>main</caller>\nhello")

    @patch('core.channel.discord.loader.BotsLoader')
    @patch('core.loaders.agents_loader.AgentsLoader')
    async def test_caller_inferred_from_context(self, mock_agents_loader, mock_bots_loader):
        from core.runtime.execution_context import current_execution_context
        from core.runtime.session_manager import SessionManager

        capture = {}
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.get_agent.return_value = _streaming_agent(
            {"channels": ["software-dev"]}, capture=capture
        )
        mock_bots_loader.return_value.find_channel.return_value = MagicMock()

        sess = SessionManager.get_session(
            agent_id="software-orchestrator", source="discord", channel="software-dev"
        )
        token = current_execution_context.set(sess)
        try:
            await stream_delegate("agent1", "hello", "software-dev")
        finally:
            current_execution_context.reset(token)

        self.assertEqual(capture["prompt"], "<caller>software-orchestrator</caller>\nhello")

    @patch('core.channel.discord.loader.BotsLoader')
    @patch('core.loaders.agents_loader.AgentsLoader')
    async def test_thread_context_is_preserved(self, mock_agents_loader, mock_bots_loader):
        import discord
        from core.runtime.execution_context import current_execution_context
        from core.runtime.session_manager import SessionManager

        capture = {}
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.get_agent.return_value = _streaming_agent(
            {"channels": ["topic-research"]}, text="agent thread response", capture=capture
        )

        mock_thread = MagicMock(spec=discord.Thread)
        mock_thread.id = 1541110915540324533
        mock_thread.name = "AI thread"
        mock_thread.parent = MagicMock(spec=discord.TextChannel)
        mock_thread.parent.name = "topic-research"

        caller_sess = SessionManager.get_session(
            agent_id="test-caller", source="discord", channel=mock_thread
        )
        token = current_execution_context.set(caller_sess)
        try:
            result = await stream_delegate(
                "topic-researcher", "explain deliberate practice", "topic-research"
            )
        finally:
            current_execution_context.reset(token)

        # The active thread is reused rather than re-resolved by name, which is
        # what keeps the callee inside the thread instead of its parent channel.
        mock_bots_loader.return_value.find_channel.assert_not_called()
        session = capture["session"]
        self.assertEqual(session.agent_id, "topic-researcher")
        self.assertEqual(session.channel_name, "topic-research")
        self.assertEqual(session.discord_thread_id, "1541110915540324533")
        self.assertEqual(session.channel_obj, mock_thread)
        self.assertEqual(result.text, "agent thread response")

    @patch('core.channel.discord.loader.BotsLoader')
    @patch('core.loaders.agents_loader.AgentsLoader')
    async def test_stateless_agent_uses_execute(self, mock_agents_loader, mock_bots_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        agent = MagicMock()
        agent.config = {
            "channels": ["*"],
            "emoji": "👷",
            "name": "Graph Worker",
            "stateless": True,
        }
        agent.execute = AsyncMock(return_value="<worker_handoff>output</worker_handoff>")
        agent.execute_stream = MagicMock()
        mock_loader.get_agent.return_value = agent
        mock_bots_loader.return_value.find_channel.return_value = MagicMock()

        result = await stream_delegate("graph-worker", "run task", "coding-pipeline")

        agent.execute.assert_called_once()
        agent.execute_stream.assert_not_called()
        self.assertEqual(result.text, "<worker_handoff>output</worker_handoff>")

    @patch('core.runtime.delegation.safe_dispatch_custom_event')
    @patch('core.channel.discord.loader.BotsLoader')
    @patch('core.loaders.agents_loader.AgentsLoader')
    async def test_streaming_events_are_dispatched(
        self, mock_agents_loader, mock_bots_loader, mock_dispatch
    ):
        from core.channel.response_parser import AgentResponse

        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        agent = MagicMock()
        agent.config = {"channels": ["software-dev"], "emoji": "🛠️", "name": "Software Planner"}

        sub_resp = AgentResponse(text="Plan created successfully.")

        async def fake_stream(prompt, session=None):
            yield {"type": "token", "content": "Plan "}
            yield {"type": "token", "content": "created successfully."}
            yield {"type": "final_response", "text": "Plan created successfully.", "response": sub_resp}

        agent.execute_stream = fake_stream
        mock_loader.get_agent.return_value = agent
        mock_bots_loader.return_value.find_channel.return_value = MagicMock()

        result = await stream_delegate("software-planner", "create plan", "software-dev")

        self.assertEqual(result.text, "Plan created successfully.")

        dispatched = [call.args[0] for call in mock_dispatch.call_args_list]
        self.assertIn(SUBAGENT_STREAM_TOKEN, dispatched)
        self.assertIn(SUBAGENT_STREAM_FINAL, dispatched)

        header_calls = [c for c in mock_dispatch.call_args_list if c.args[1].get("is_header") is True]
        self.assertEqual(len(header_calls), 1)
        self.assertEqual(header_calls[0].args[1]["content"], "🛠️ Software Planner: ")

    @patch('core.runtime.delegation.safe_dispatch_custom_event')
    @patch('core.channel.discord.loader.BotsLoader')
    @patch('core.loaders.agents_loader.AgentsLoader')
    async def test_reaction_is_announced_before_any_output(
        self, mock_agents_loader, mock_bots_loader, mock_dispatch
    ):
        """The emoji has to come from here, not from a tool callback, or the
        deterministic path loses it entirely."""
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.get_agent.return_value = _streaming_agent(
            {"channels": ["receipts"], "emoji": "🧾", "name": "Receipt Processor"}
        )
        mock_bots_loader.return_value.find_channel.return_value = MagicMock()

        await stream_delegate("receipt-processor", "scan this", "receipts")

        dispatched = [call.args[0] for call in mock_dispatch.call_args_list]
        self.assertEqual(dispatched[0], ROUTE_REACTION, "reaction must be the first event")

        payload = mock_dispatch.call_args_list[0].args[1]
        self.assertEqual(payload["agent_id"], "receipt-processor")
        self.assertEqual(payload["emoji"], "🧾")

    @patch('core.runtime.delegation.safe_dispatch_custom_event')
    @patch('core.channel.discord.loader.BotsLoader')
    @patch('core.loaders.agents_loader.AgentsLoader')
    async def test_reaction_announced_for_async_delegation_too(
        self, mock_agents_loader, mock_bots_loader, mock_dispatch
    ):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        agent = MagicMock()
        agent.config = {"channels": ["receipts"], "emoji": "🧾"}
        agent.execute = AsyncMock(return_value="ok")
        mock_loader.get_agent.return_value = agent
        mock_bots_loader.return_value.find_channel.return_value = MagicMock()

        await stream_delegate("receipt-processor", "scan", "receipts", run_async=True)
        await asyncio.sleep(0.05)

        dispatched = [call.args[0] for call in mock_dispatch.call_args_list]
        self.assertIn(ROUTE_REACTION, dispatched)

    @patch('core.loaders.agents_loader.AgentsLoader')
    async def test_unknown_agent_is_reported(self, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.get_agent.side_effect = ValueError("Agent configuration not found for: nope")

        result = await stream_delegate("nope", "hello", "general")

        self.assertFalse(result.ok)
        self.assertIn("Unknown agent 'nope'", result.error)


if __name__ == '__main__':
    unittest.main()
