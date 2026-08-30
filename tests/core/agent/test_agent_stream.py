import unittest
import os
import sys
from unittest.mock import patch, MagicMock, AsyncMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.agent.agent import Agent
from core.agent.session_manager import SessionManager

class TestAgentStream(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from core.agent.job_manager import JobManager
        JobManager._instance = None

    @patch('core.agent.agent.LoggingHandler')
    async def test_execute_stream_yields_tokens_and_final(self, mock_logging_handler_class):
        mock_graph = MagicMock()
        
        async def mock_astream_events(*args, **kwargs):
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": MagicMock(content="Hello ")}
            }
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": MagicMock(content="world!")}
            }
            yield {
                "event": "on_tool_start",
                "name": "agent_call",
                "data": {"input": {"agent_id": "day-planner"}},
                "run_id": "run_123"
            }

        mock_graph.astream_events = mock_astream_events
        mock_state = MagicMock()
        mock_state.values = {"messages": [MagicMock(content="Hello world!")]}
        mock_state.next = []
        mock_graph.get_state.return_value = mock_state

        agent = Agent("test-agent", {})
        agent.graph = mock_graph

        session = SessionManager.get_session(agent_id="test-agent", source="discord", channel="general")
        events = []
        async for event in agent.execute_stream("Hi", session=session):
            events.append(event)

        types = [e["type"] for e in events]
        self.assertIn("token", types)
        self.assertIn("tool_start", types)
        self.assertIn("final_response", types)

        final_event = [e for e in events if e["type"] == "final_response"][0]
        self.assertEqual(final_event["text"], "Hello world!")

    @patch('core.agent.agent.LoggingHandler')
    async def test_execute_stream_handles_exception(self, mock_logging_handler_class):
        mock_graph = MagicMock()

        async def mock_astream_events_failing(*args, **kwargs):
            if False:
                yield None
            err_msg = "503 Service Unavailable. {'message': '{\\n  \"error\": {\\n    \"code\": 503,\\n    \"message\": \"This model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again later.\",\\n    \"status\": \"UNAVAILABLE\"\\n  }\\n}\\n', 'status': 'Service Unavailable'}"
            raise Exception(err_msg)

        mock_graph.astream_events = mock_astream_events_failing

        agent = Agent("test-agent", {})
        agent.graph = mock_graph

        mock_channel = AsyncMock()
        mock_channel.name = "general"
        session = SessionManager.get_session("test-agent", source="discord", channel=mock_channel)
        events = []
        async for event in agent.execute_stream("Hi", session=session):
            events.append(event)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "error")
        expected_msg = "[503] This model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again later."
        self.assertEqual(events[0]["content"], expected_msg)
        mock_channel.send.assert_called_once_with(expected_msg)

    @patch('core.agent.agent.LoggingHandler')
    async def test_execute_stream_handles_unexpected_error(self, mock_logging_handler_class):
        mock_graph = MagicMock()

        async def mock_astream_events_failing(*args, **kwargs):
            if False:
                yield None
            raise KeyError("unexpected_missing_field")

        mock_graph.astream_events = mock_astream_events_failing

        agent = Agent("test-agent", {})
        agent.graph = mock_graph

        session = SessionManager.get_session(agent_id="test-agent", source="discord", channel="general")
        events = []
        async for event in agent.execute_stream("Hi", session=session):
            events.append(event)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "error")
        self.assertEqual(events[0]["content"], "'unexpected_missing_field'")

    @patch('core.agent.agent.SqliteCheckpointer')
    @patch('core.agent.agent.LoggingHandler')
    async def test_execute_stream_recovers_from_corrupt_checkpoint(self, mock_logging_handler_class, mock_checkpointer_class):
        mock_checkpointer = MagicMock()
        mock_checkpointer_class.return_value = mock_checkpointer

        mock_graph = MagicMock()
        attempts = 0

        async def mock_astream_events(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise Exception("tool_calls that do not have a corresponding ToolMessage in checkpoint")
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": MagicMock(content="Recovered answer")}
            }

        mock_graph.astream_events = mock_astream_events
        mock_state = MagicMock()
        mock_state.values = {"messages": [MagicMock(content="Recovered answer")]}
        mock_state.next = []
        mock_graph.get_state.return_value = mock_state

        agent = Agent("test-agent", {})
        agent.graph = mock_graph

        session = SessionManager.get_session(agent_id="test-agent", source="discord", channel="general")
        events = []
        async for event in agent.execute_stream("Hello", session=session):
            events.append(event)

        mock_checkpointer.rollback_last_step.assert_called_once()
        self.assertEqual(attempts, 2)
        final_events = [e for e in events if e["type"] == "final_response"]
        self.assertEqual(len(final_events), 1)
        self.assertEqual(final_events[0]["text"], "Recovered answer")

    @patch('core.agent.agent.LoggingHandler')
    async def test_execute_stream_handles_subagent_custom_events(self, mock_logging_handler_class):
        mock_graph = MagicMock()
        from core.agent.agent_response import AgentResponse
        from core.agent.stream_handler import (
            SUBAGENT_STREAM_TOKEN,
            SUBAGENT_STREAM_FINAL,
            EVENT_TOKEN,
            EVENT_FINAL_RESPONSE,
        )

        sub_resp = AgentResponse(text="Subagent final text", poll_data={"options": ["A", "B"]})

        async def mock_astream_events(*args, **kwargs):
            yield {
                "event": "on_custom_event",
                "name": SUBAGENT_STREAM_TOKEN,
                "data": {"content": "🔍 Researcher: ", "agent_id": "topic-researcher", "is_header": True}
            }
            yield {
                "event": "on_custom_event",
                "name": SUBAGENT_STREAM_TOKEN,
                "data": {"content": "Subagent final text", "agent_id": "topic-researcher"}
            }
            yield {
                "event": "on_custom_event",
                "name": SUBAGENT_STREAM_FINAL,
                "data": {"agent_id": "topic-researcher", "response": sub_resp, "text": "Subagent final text"}
            }

        mock_graph.astream_events = mock_astream_events
        agent = Agent("main", {})
        agent.graph = mock_graph

        session = SessionManager.get_session(agent_id="main", source="discord", channel="general")
        events = []
        async for event in agent.execute_stream("Research AI", session=session):
            events.append(event)

        token_events = [e for e in events if e["type"] == EVENT_TOKEN]
        self.assertEqual(len(token_events), 2)
        self.assertEqual(token_events[0]["content"], "🔍 Researcher: ")
        self.assertEqual(token_events[1]["content"], "Subagent final text")

        final_events = [e for e in events if e["type"] == EVENT_FINAL_RESPONSE]
        self.assertEqual(len(final_events), 1)
        self.assertEqual(final_events[0]["text"], "Subagent final text")
        self.assertEqual(final_events[0]["poll_data"], {"options": ["A", "B"]})
        self.assertEqual(final_events[0]["response"], sub_resp)

if __name__ == "__main__":
    unittest.main()
