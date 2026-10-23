"""DiscordStreamBuffer: the live, edited Discord message a streamed reply is rendered into."""
import asyncio
import re
import unittest
from unittest.mock import AsyncMock, MagicMock

import discord

from core.channel.discord.stream_buffer import DiscordStreamBuffer


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

    def test_filter_xml_for_stream_keeps_literal_less_than(self):
        text = (
            "Free for Infants (<1 yr).\nWebsite: [CDM](https://cdm.org)\nMore.\n"
            '<poll allow_multiple="false"><question>Q?</question></poll>'
        )
        filtered = DiscordStreamBuffer.filter_xml_for_stream(text)
        self.assertEqual(
            filtered,
            "Free for Infants (<1 yr).\nWebsite: [CDM](https://cdm.org)\nMore."
        )

    def test_filter_xml_for_stream_hides_poll_with_attributes_midtext(self):
        text = 'A <poll allow_multiple="true"><question>Q</question></poll> B'
        filtered = DiscordStreamBuffer.filter_xml_for_stream(text)
        self.assertEqual(filtered, "A  B")

    def test_filter_xml_for_stream_hides_partial_known_tag_opener(self):
        for tail in ["<", "<po", '<poll allow_multiple="fa', "<system_mem"]:
            filtered = DiscordStreamBuffer.filter_xml_for_stream("Hi " + tail)
            self.assertEqual(filtered, "Hi", tail)

    def test_filter_xml_for_stream_keeps_non_tag_trailing_less_than(self):
        for text in ["x <1 yr", "a < b", "price <$20"]:
            self.assertEqual(
                DiscordStreamBuffer.filter_xml_for_stream(text), text
            )

    def test_filter_xml_for_stream_hides_speech(self):
        """The backstop for any path that skips the voice router: speech, closed,
        still open, or half-streamed, never shows in the channel."""
        closed = "<speech>Two meetings today.</speech>\n**Today**: standup, dentist"
        self.assertEqual(DiscordStreamBuffer.filter_xml_for_stream(closed), "\n**Today**: standup, dentist")
        self.assertEqual(DiscordStreamBuffer.filter_xml_for_stream("Hi <speech>Two meet"), "Hi")
        for tail in ["<sp", "<speech", "<spee"]:
            self.assertEqual(DiscordStreamBuffer.filter_xml_for_stream("Hi " + tail), "Hi", tail)

    async def test_speech_across_a_message_boundary_is_hidden_as_a_unit(self):
        """Hidden tags are filtered before the text is split into messages."""
        channel = AsyncMock()
        channel.send.return_value = AsyncMock()
        buffer = DiscordStreamBuffer(channel, edit_interval=0, max_chunk_size=100)
        speech = "<speech>" + "word " * 60 + "</speech>"
        await buffer.append_token("Intro line.\n" + speech + "\nShown after.")
        await buffer.finalize("")
        posted = "".join(call.args[0] for call in channel.send.call_args_list)
        self.assertNotIn("word", posted)
        self.assertNotIn("speech", posted)
        self.assertIn("Shown after.", posted)

    async def test_a_reply_that_was_only_speech_still_reaches_the_channel(self):
        from core.channel.response_parser import AgentResponse
        channel = AsyncMock()
        channel.send.return_value = AsyncMock()
        buffer = DiscordStreamBuffer(channel, edit_interval=60)
        response = AgentResponse.from_string("<speech>Done, the lights are off.</speech>")
        await buffer.append_token("\n\n")  # the text lane only ever saw the whitespace after it
        await buffer.finalize(response.text, response=response)
        channel.send.assert_awaited_once_with("Done, the lights are off.")

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

    @staticmethod
    def _fences_balanced(chunk):
        return len(re.findall(r"`{3,}", chunk)) % 2 == 0

    async def test_live_preview_closes_an_open_block_until_it_ends(self):
        mock_channel = AsyncMock()
        mock_msg = AsyncMock()
        mock_channel.send.return_value = mock_msg
        buffer = DiscordStreamBuffer(mock_channel, edit_interval=0)

        await buffer.append_token('Here:\n```json\n{"a": 1')
        mock_channel.send.assert_called_once_with('Here:\n```json\n{"a": 1\n```')

        await buffer.append_token('}\n```\nDone.')
        await buffer.finalize(final_text="")
        mock_msg.edit.assert_called_with(content='Here:\n```json\n{"a": 1}\n```\nDone.')

    async def test_render_keeps_code_blocks_balanced_across_messages(self):
        mock_channel = AsyncMock()
        mock_channel.send.side_effect = lambda *args, **kwargs: AsyncMock()
        buffer = DiscordStreamBuffer(mock_channel, edit_interval=0, max_chunk_size=200)

        code = "\n".join(f"x{i} = {i}" for i in range(60))
        await buffer.append_token(f"Intro\n```python\n{code}")  # block still streaming

        sent = [c.args[0] for c in mock_channel.send.call_args_list]
        self.assertGreater(len(sent), 1)
        for chunk in sent:
            self.assertLessEqual(len(chunk), 200)
            self.assertTrue(self._fences_balanced(chunk), chunk)
        self.assertTrue(all(c.startswith("```python\n") for c in sent[1:]))

    async def test_finalize_splits_without_breaking_words_or_code_blocks(self):
        mock_channel = AsyncMock()
        mock_channel.send.side_effect = lambda *args, **kwargs: AsyncMock()
        buffer = DiscordStreamBuffer(mock_channel, edit_interval=60, max_chunk_size=200)

        prose = " ".join(["alpha", "beta", "gamma"] * 40)
        code = "\n".join(f"x{i} = {i}" for i in range(40))
        text = f"{prose}\n```python\n{code}\n```\nDone."
        await buffer.finalize(final_text=text)

        sent = [c.args[0] for c in mock_channel.send.call_args_list]
        self.assertGreater(len(sent), 1)
        for chunk in sent:
            self.assertLessEqual(len(chunk), 200)
            self.assertTrue(self._fences_balanced(chunk), chunk)
        fence = re.compile(r"`{3,}\w*")
        self.assertEqual(fence.sub(" ", "\n".join(sent)).split(), fence.sub(" ", text).split())

    async def test_hidden_block_crossing_a_message_boundary_is_hidden_whole(self):
        # Tags are filtered on the whole text before it is split, so a block
        # spanning two messages can't leak its tail into the second one.
        mock_channel = AsyncMock()
        mock_channel.send.side_effect = lambda *args, **kwargs: AsyncMock()
        buffer = DiscordStreamBuffer(mock_channel, edit_interval=0, max_chunk_size=100)

        log = "\n".join(f"- [12:00:0{i}] [MEMORY] private note {i}" for i in range(5))
        await buffer.append_token(f"{'Visible start. ' * 5}\n<system_memory_log>\n{log}")
        await buffer.append_token("\n</system_memory_log>\nVisible end.")
        await buffer.finalize(final_text="")

        rendered = [c.args[0] for c in mock_channel.send.call_args_list]
        for msg in buffer.messages:
            rendered += [c.kwargs["content"] for c in msg.edit.call_args_list]
        rendered = "\n".join(rendered)
        self.assertIn("Visible end.", rendered)
        self.assertNotIn("private note", rendered)
        self.assertNotIn("system_memory_log", rendered)

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
