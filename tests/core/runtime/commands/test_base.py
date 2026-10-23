"""The pattern every command follows: answering, how a typed command is
recognized, and who answers each kind."""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.runtime.commands.base import (
    ChannelCommand,
    Command,
    CommandContext,
    TypedCommand,
    TypedContext,
    VoiceRoomCommand,
)


class TestCommandContext(unittest.IsolatedAsyncioTestCase):

    async def test_send_posts_one_message_as_it_is(self):
        channel = MagicMock(send=AsyncMock())
        await CommandContext(channel=channel).send("Session context cleared. ")
        channel.send.assert_awaited_once_with("Session context cleared. ")

    async def test_reply_splits_a_long_answer_into_discord_sized_messages(self):
        channel = MagicMock(send=AsyncMock())
        text = "\n".join(f"line {i}: " + "x" * 80 for i in range(60))
        await CommandContext(channel=channel).reply(text)
        sent = [c.args[0] for c in channel.send.await_args_list]
        self.assertGreater(len(sent), 1)
        self.assertTrue(all(len(chunk) <= 2000 for chunk in sent))
        self.assertEqual("\n".join(sent), text)

    async def test_reply_pauses_between_chunks_when_asked(self):
        channel = MagicMock(send=AsyncMock())
        text = "\n".join("y" * 100 for _ in range(40))
        with patch("core.runtime.commands.base.asyncio.sleep", AsyncMock()) as sleep:
            await CommandContext(channel=channel).reply(text, pause_s=1)
        self.assertEqual(sleep.await_count, channel.send.await_count - 1)
        sleep.assert_awaited_with(1)

    async def test_nothing_is_sent_without_a_channel_that_can_send(self):
        for channel in (None, "general"):
            ctx = CommandContext(channel=channel)
            await ctx.send("hello")
            await ctx.reply("hello")  # neither raises


class _Echo(TypedCommand):
    name = "echo"
    aliases = ("say",)


class TestCommands(unittest.IsolatedAsyncioTestCase):

    async def test_a_command_is_answered_unless_it_says_otherwise_and_must_implement_run(self):
        command = Command()
        self.assertTrue(command.available(CommandContext()))
        with self.assertRaises(NotImplementedError):
            await command.run(CommandContext(), "")
        with self.assertRaises(NotImplementedError):
            command.match("anything")

    def test_a_typed_command_is_bang_then_its_name_or_an_alias_then_its_arguments(self):
        echo = _Echo()
        self.assertEqual(echo.match("!echo"), "")
        self.assertEqual(echo.match("!say  hello there \n"), "hello there")
        self.assertEqual(echo.match("!echo two\nlines"), "two\nlines")
        for text in ("echo", "! echo", "!echoes", "!Echo", " !echo", "!", "", None):
            self.assertIsNone(echo.match(text), text)

    def test_a_channel_command_is_answered_by_the_bot_that_answers_the_channel(self):
        command = ChannelCommand()
        self.assertTrue(command.available(TypedContext(answers_here=True)))
        self.assertFalse(command.available(TypedContext(answers_here=False)))

    def test_a_voice_room_command_needs_the_room_and_maybe_the_voice_host(self):
        room = MagicMock()
        anyone, host_only = VoiceRoomCommand(), VoiceRoomCommand()
        host_only.voice_host_only = True
        self.assertFalse(anyone.available(TypedContext(voice=None)))
        self.assertTrue(anyone.available(TypedContext(voice=room)))
        for hosts in (True, False):
            room.hosts_voice.return_value = hosts
            self.assertIs(host_only.available(TypedContext(voice=room)), hosts)


if __name__ == "__main__":
    unittest.main()
