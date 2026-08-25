import unittest
from unittest.mock import patch, MagicMock, AsyncMock
from core.agent.agent import Agent

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

        events = []
        async for event in agent.execute_stream("Hi", source="discord"):
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
        events = []
        async for event in agent.execute_stream("Hi", source="discord", channel=mock_channel):
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

        events = []
        async for event in agent.execute_stream("Hi", source="discord"):
            events.append(event)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "error")
        self.assertEqual(events[0]["content"], "'unexpected_missing_field'")

if __name__ == "__main__":
    unittest.main()
