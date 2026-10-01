"""Replying to a Discord message tells the agent which message is meant."""
import datetime
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from core.channel.discord.runner import (
    REPLY_EXCERPT_CHARS,
    BotRunner,
    append_reply_context,
    format_reply_context,
)

UTC = datetime.timezone.utc


class TestFormat(unittest.TestCase):
    def test_block_has_author_time_and_excerpt(self):
        at = datetime.datetime(2026, 10, 1, 14, 0, tzinfo=UTC)
        with patch("core.util.time_util.get_local_timezone", return_value=UTC):
            block = format_reply_context("Concierge", at, "Today's plan")
        self.assertEqual(block, '<replying_to author="Concierge" posted="2026-10-01 14:00 UTC">\nToday\'s plan\n</replying_to>')

    def test_long_message_is_truncated(self):
        block = format_reply_context("x", None, "y" * (REPLY_EXCERPT_CHARS + 10))
        self.assertIn("[...]", block)
        self.assertNotIn("posted=", block)

    def test_appended_after_user_text_so_prefixes_still_match(self):
        self.assertEqual(append_reply_context("[main] what?", "<r/>"), "[main] what?\n\n<r/>")
        parts = append_reply_context([{"type": "text", "text": "kill job"}], "<r/>")
        self.assertEqual(parts[0]["text"], "kill job")
        self.assertEqual(parts[-1]["text"], "\n\n<r/>")


class TestResolve(unittest.IsolatedAsyncioTestCase):
    def runner(self):
        return SimpleNamespace(agent_id="main")

    def message(self, reference, fetched=None, fetch_error=None):
        channel = MagicMock()
        channel.fetch_message = AsyncMock(return_value=fetched, side_effect=fetch_error)
        return SimpleNamespace(reference=reference, channel=channel)

    async def test_not_a_reply(self):
        self.assertIsNone(await BotRunner._reply_context(self.runner(), self.message(None)))
        # Test doubles with an auto-attribute `reference` are not replies either.
        self.assertIsNone(await BotRunner._reply_context(self.runner(), self.message(MagicMock())))

    async def test_reply_is_fetched_and_formatted(self):
        ref = discord.MessageReference(message_id=123, channel_id=1)
        target = SimpleNamespace(content="Scheduled plan", author=SimpleNamespace(display_name="Concierge"), created_at=None)
        msg = self.message(ref, fetched=target)
        block = await BotRunner._reply_context(self.runner(), msg)
        msg.channel.fetch_message.assert_awaited_once_with(123)
        self.assertIn('author="Concierge"', block)
        self.assertIn("Scheduled plan", block)

    async def test_fetch_failure_or_empty_content_yields_nothing(self):
        ref = discord.MessageReference(message_id=123, channel_id=1)
        self.assertIsNone(await BotRunner._reply_context(self.runner(), self.message(ref, fetch_error=RuntimeError("gone"))))
        empty = SimpleNamespace(content="  ", author=None, created_at=None)
        self.assertIsNone(await BotRunner._reply_context(self.runner(), self.message(ref, fetched=empty)))


if __name__ == "__main__":
    unittest.main()
