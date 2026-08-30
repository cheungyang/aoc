import unittest
import asyncio
import os
import sys
from unittest.mock import patch, MagicMock, AsyncMock
import discord

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from core.agent.stream_handler import DiscordStreamBuffer, StreamHandler, EVENT_TOKEN, EVENT_FINAL_RESPONSE

class TestDiscordStreamBuffer(unittest.IsolatedAsyncioTestCase):
    def test_filter_xml_for_stream_hides_tags(self):
        text = "Hello here is text <poll><question>Test</question></poll> and more text"
        filtered = DiscordStreamBuffer.filter_xml_for_stream(text)
        self.assertEqual(filtered, "Hello here is text  and more text")

    def test_filter_xml_for_stream_hides_unclosed_trailing_tag(self):
        text = "Here is my answer. <poll><question>Should we"
        filtered = DiscordStreamBuffer.filter_xml_for_stream(text)
        self.assertEqual(filtered, "Here is my answer.")

    def test_filter_xml_for_stream_hides_system_memory_log(self):
        text = "Finished task.\n<system_memory_log>\n- [12:00:00] [MEMORY] Done.\n</system_memory_log>"
        filtered = DiscordStreamBuffer.filter_xml_for_stream(text)
        self.assertEqual(filtered, "Finished task.")

    async def test_streaming_buffer_sends_and_throttles(self):
        mock_channel = AsyncMock()
        mock_msg = AsyncMock()
        mock_channel.send.return_value = mock_msg

        buffer = DiscordStreamBuffer(mock_channel, edit_interval=0.05)

        # Append first token -> sends message
        await buffer.append_token("Hello ")
        mock_channel.send.assert_called_once_with("Hello")

        # Append second token before interval -> edit throttled
        await buffer.append_token("world!")
        
        # Wait interval and finalize
        await asyncio.sleep(0.06)
        await buffer.finalize(final_text="Hello world!")

        mock_msg.edit.assert_called()

    async def test_streaming_buffer_handles_unknown_channel_404(self):
        mock_channel = AsyncMock()
        mock_response = MagicMock()
        mock_response.status = 404
        mock_response.reason = "Not Found"
        mock_channel.send.side_effect = discord.NotFound(mock_response, {"code": 10003, "message": "Unknown Channel"})

        buffer = DiscordStreamBuffer(mock_channel, edit_interval=0.01)

        # First token fails with 404 and disables stream buffer
        await buffer.append_token("Hello ")
        self.assertEqual(mock_channel.send.call_count, 1)
        self.assertTrue(buffer._disabled)

        # Subsequent token flushes and finalize should be suppressed
        await asyncio.sleep(0.02)
        await buffer.append_token("world!")
        await buffer.finalize(final_text="Hello world!")
        self.assertEqual(mock_channel.send.call_count, 1)

    async def test_streaming_buffer_handles_none_channel(self):
        buffer = DiscordStreamBuffer(None, edit_interval=0.01)
        self.assertTrue(buffer._disabled)
        await buffer.append_token("Hello ")
        await buffer.finalize(final_text="Hello")


class TestStreamHandler(unittest.IsolatedAsyncioTestCase):
    async def test_stream_graph_events_chat_and_tools(self):
        mock_graph = MagicMock()

        async def mock_astream_events(*args, **kwargs):
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": MagicMock(content="Hello ")}
            }
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": MagicMock(content="world")}
            }
            yield {
                "event": "on_tool_start",
                "name": "calc",
                "data": {"input": {"x": 1}},
                "run_id": "run_1"
            }
            yield {
                "event": "on_tool_end",
                "name": "calc",
                "data": {"output": "2"},
                "run_id": "run_1"
            }

        mock_graph.astream_events = mock_astream_events
        events = []
        async for ev in StreamHandler.stream_graph_events(mock_graph, {}, {}):
            events.append(ev)

        self.assertEqual(len(events), 4)
        self.assertEqual(events[0], {"type": "token", "content": "Hello "})
        self.assertEqual(events[1], {"type": "token", "content": "world"})
        self.assertEqual(events[2], {"type": "tool_start", "tool_name": "calc", "tool_args": {"x": 1}, "run_id": "run_1"})
        self.assertEqual(events[3], {"type": "tool_end", "tool_name": "calc", "output": "2", "run_id": "run_1"})

    async def test_stream_with_recovery_on_corrupt_checkpoint(self):
        mock_graph = MagicMock()
        attempts = 0

        async def mock_astream_events(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise Exception("tool_calls that do not have a corresponding ToolMessage")
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": MagicMock(content="Recovered")}
            }

        mock_graph.astream_events = mock_astream_events
        recovered_sessions = []

        def recover_fn(session):
            recovered_sessions.append(session)

        def is_corrupt_fn(e):
            return "tool_calls that do not have a corresponding ToolMessage" in str(e)

        session = MagicMock()
        events = []
        async for ev in StreamHandler.stream_with_recovery(
            mock_graph, {}, {}, session, recover_fn, is_corrupt_fn
        ):
            events.append(ev)

        self.assertEqual(attempts, 2)
        self.assertEqual(len(recovered_sessions), 1)
        self.assertEqual(events, [{"type": "token", "content": "Recovered"}])

    async def test_stream_graph_events_suppresses_post_subagent_chat_tokens(self):
        from core.agent.stream_handler import SUBAGENT_STREAM_TOKEN, SUBAGENT_STREAM_FINAL
        mock_graph = MagicMock()

        async def mock_astream_events(*args, **kwargs):
            # 1. Subagent emits header and content tokens
            yield {
                "event": "on_custom_event",
                "name": SUBAGENT_STREAM_TOKEN,
                "data": {"content": "🔬 Ted: ", "agent_id": "topic-researcher", "is_header": True}
            }
            yield {
                "event": "on_custom_event",
                "name": SUBAGENT_STREAM_TOKEN,
                "data": {"content": "Research findings complete.", "agent_id": "topic-researcher"}
            }
            yield {
                "event": "on_custom_event",
                "name": SUBAGENT_STREAM_FINAL,
                "data": {"agent_id": "topic-researcher", "text": "Research findings complete."}
            }
            # 2. Main orchestrator LLM generates turn 2 closing tokens
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": MagicMock(content="Here is what Ted found: Research findings complete.")}
            }

        mock_graph.astream_events = mock_astream_events
        events = []
        async for ev in StreamHandler.stream_graph_events(mock_graph, {}, {}):
            events.append(ev)

        token_contents = [ev["content"] for ev in events if ev["type"] == "token"]
        self.assertEqual(token_contents, ["🔬 Ted: ", "Research findings complete."])
        # Main's post-subagent chat model tokens must be suppressed to avoid repeating text
        self.assertNotIn("Here is what Ted found", "".join(token_contents))

    async def test_finalize_deletes_extra_message_chunks(self):
        mock_channel = AsyncMock()
        mock_msg1 = AsyncMock()
        mock_msg2 = AsyncMock()
        mock_channel.send.side_effect = [mock_msg1, mock_msg2]

        buffer = DiscordStreamBuffer(mock_channel, edit_interval=0.01, max_chunk_size=100)
        # Live stream grows across 2 chunks (>100 chars)
        await buffer.append_token("A" * 120)
        await asyncio.sleep(0.02)
        await buffer._render()
        self.assertEqual(len(buffer.messages), 2)

        # Finalize with shorter text (fits in 1 chunk of <100 chars)
        buffer.accumulated_text = "B" * 50
        await buffer.finalize(final_text="B" * 50)

        # Message 1 edited, message 2 deleted
        mock_msg1.edit.assert_called_with(content="B" * 50)
        mock_msg2.delete.assert_called_once()
        self.assertEqual(len(buffer.messages), 1)


if __name__ == "__main__":
    unittest.main()
