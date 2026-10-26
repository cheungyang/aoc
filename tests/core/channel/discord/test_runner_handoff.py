"""A hand-off, as the Discord runner shows a typed one (graphs/main's HANDOFF).

The channel the message came from keeps the pointer; the named agent's
channel gets what was asked, then the reply, and the channel inbox counts the
reply where it landed.
"""
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from core.channel.discord.runner import BotRunner
from core.channel.handoff import REQUEST_CHARS, channel_named, pointer_line, request_line

HANDOFF = {"type": "handoff", "agent_id": "topic-researcher", "name": "Ted", "emoji": "🔬",
           "channel": "topic-research", "pointer": "→ 🔬 Ted has this in #topic-research",
           "request": "Ted, create a syllabus on THIS topic"}


def _channel(name, mention):
    channel = MagicMock()
    channel.name = name
    channel.mention = mention
    channel.send = AsyncMock()
    typing = MagicMock()
    typing.__aenter__ = AsyncMock()
    typing.__aexit__ = AsyncMock()
    channel.typing.return_value = typing
    return channel


class _Buffer:
    """DiscordStreamBuffer, recording what each channel was shown."""
    made = []

    def __init__(self, channel, edit_interval=1.5):
        self.channel, self.tokens, self.final = channel, [], None
        _Buffer.made.append(self)

    async def append_token(self, token):
        self.tokens.append(token)

    async def finalize(self, final_text, response=None):
        self.final = (final_text, response)


class TestTypedHandoff(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        _Buffer.made = []
        self.wiki = _channel("pkm-wiki", "<#1>")
        self.research = _channel("topic-research", "<#2>")

    async def _run(self, events, guild_channels):
        with patch("core.channel.discord.runner.commands.Bot") as bot_class, \
             patch("core.channel.discord.runner.AgentsLoader") as loader_class, \
             patch("core.channel.discord.stream_buffer.DiscordStreamBuffer", _Buffer), \
             patch("core.channel.discord.runner.record_reply") as record_reply, \
             patch("core.channel.discord.runner.mark_read"):
            bot_class.return_value = MagicMock()
            runner = BotRunner("test_token", "main")

            async def stream(*args, **kwargs):
                for event in events:
                    yield event

            agent = MagicMock()
            agent.config = {"channel_hosts": ["pkm-wiki", "topic-research"]}
            agent.execute_stream = stream
            loader_class.return_value.get_agent.return_value = agent

            message = MagicMock()
            message.author = MagicMock(bot=False, display_name="Alva")
            message.content = HANDOFF["request"]
            message.mentions = []
            message.attachments = []
            message.channel = self.wiki
            message.guild.text_channels = guild_channels
            await runner.on_message(message)
        return record_reply

    def _events(self):
        response = MagicMock(text="Starting a syllabus on prompt caching.")
        return [
            HANDOFF,
            {"type": "reaction", "agent_id": "topic-researcher", "emoji": "🔬"},
            {"type": "token", "content": "🔬 Ted: ", "agent_id": "topic-researcher", "is_header": True},
            {"type": "token", "content": "Starting a syllabus on prompt caching.", "agent_id": "topic-researcher"},
            {"type": "final_response", "text": "Starting a syllabus on prompt caching.", "response": response},
        ], response

    async def test_the_reply_lands_in_the_named_agents_channel(self):
        events, response = self._events()
        record_reply = await self._run(events, [self.wiki, self.research])

        here, there = _Buffer.made
        self.assertIs(here.channel, self.wiki)
        self.assertEqual((here.tokens, here.final), ([], ("→ 🔬 Ted has this in <#2>", None)))
        self.research.send.assert_awaited_once_with(
            "↪️ **Alva** in <#1>: Ted, create a syllabus on THIS topic")
        self.assertIs(there.channel, self.research)
        self.assertEqual(there.tokens, ["🔬 Ted: ", "Starting a syllabus on prompt caching."])
        self.assertEqual(there.final, ("Starting a syllabus on prompt caching.", response))
        record_reply.assert_called_once()
        self.assertIs(record_reply.call_args.args[0], self.research)
        self.assertEqual(record_reply.call_args.kwargs["agent_id"], "topic-researcher")

    async def test_a_channel_that_cant_be_found_keeps_the_reply_here(self):
        events, _ = self._events()
        record_reply = await self._run(events, [self.wiki])

        (only,) = _Buffer.made
        self.assertIs(only.channel, self.wiki)
        self.assertEqual(only.tokens[-1], "Starting a syllabus on prompt caching.")
        self.assertIs(record_reply.call_args.args[0], self.wiki)


class TestHandoffLines(unittest.TestCase):
    """core/channel/handoff.py: how the channels word it."""

    def test_the_pointer_links_the_channel_when_it_can(self):
        target = MagicMock(mention="<#2>")
        self.assertEqual(pointer_line(HANDOFF, target), "→ 🔬 Ted has this in <#2>")
        self.assertEqual(pointer_line(HANDOFF, object()), "→ 🔬 Ted has this in #topic-research")

    def test_the_request_says_who_asked_where(self):
        source = MagicMock(mention="<#1>")
        self.assertEqual(request_line("Alva", source, HANDOFF), "↪️ **Alva** in <#1>: Ted, create a syllabus on THIS topic")
        self.assertEqual(request_line("Sam", "general", HANDOFF, spoken=True),
                         "↪️ 🎤 **Sam** in #general: Ted, create a syllabus on THIS topic")

    def test_a_long_request_is_cut_to_fit_a_message(self):
        line = request_line("Alva", "general", {"request": "w " * 2000})
        self.assertLess(len(line), 2000)
        self.assertTrue(line.endswith(" [...]"))
        self.assertLessEqual(len(line.split(": ", 1)[1]), REQUEST_CHARS + len(" [...]"))

    def test_a_channel_by_name(self):
        research = MagicMock()
        research.name = "topic-research"
        guild = MagicMock(text_channels=[research])
        self.assertIs(channel_named(guild, "#Topic-Research"), research)
        self.assertIsNone(channel_named(guild, "real-estate"))
        self.assertIsNone(channel_named(None, "topic-research"))


if __name__ == "__main__":
    unittest.main()
