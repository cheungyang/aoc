import unittest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
import discord
from core.agent.streaming_handler import DiscordStreamBuffer

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

if __name__ == "__main__":
    unittest.main()
