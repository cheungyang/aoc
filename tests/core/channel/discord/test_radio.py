"""Team radio's Discord side (core/channel/discord/radio.py), against stand-ins for discord.py."""
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from core.channel.discord import radio
from core.channel.discord.radio import THREAD_NAME, DiscordRadio


class Ownership:
    def __init__(self, mine=True, ready=True, role="prod"):
        self.mine, self.ready, self.role = mine, ready, role

    async def wait_ready(self):
        return self.ready

    def is_mine(self, agent_id):
        return self.mine


class Thread:
    def __init__(self, name=THREAD_NAME, archived=False):
        self.name, self.archived, self.sent = name, archived, []

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))
        message = MagicMock(id=100 + len(self.sent))
        message.add_reaction = AsyncMock()
        self.last = message
        return message

    def get_partial_message(self, message_id):
        return f"ref:{message_id}"

    async def edit(self, archived):
        self.archived = archived
        return self


class Lobby:
    name = "general"

    def __init__(self, threads=(), archived=()):
        self.threads, self._archived = list(threads), list(archived)
        self.created = []

    async def archived_threads(self, limit=None):
        for thread in self._archived:
            yield thread

    async def create_thread(self, name, type, auto_archive_duration):
        thread = Thread(name)
        self.created.append((name, type, auto_archive_duration))
        return thread


def _radio(lobby, ownership=None):
    client = MagicMock()
    client.wait_until_ready = AsyncMock()
    client.guilds = [MagicMock(text_channels=[lobby])]
    bots = MagicMock()
    bots.get_bot.return_value = MagicMock(bot=client)
    return DiscordRadio(bots=bots, ownership=ownership or Ownership())


@pytest.mark.asyncio
async def test_a_line_goes_into_the_existing_thread_with_its_reactions():
    thread = Thread()
    message_id = await _radio(Lobby(threads=[thread])).post("📻 #1 line", ("✅", "❌"))
    assert message_id == "101"
    assert thread.sent[0][0] == "📻 #1 line"
    assert [c.args[0] for c in thread.last.add_reaction.await_args_list] == ["✅", "❌"]


@pytest.mark.asyncio
async def test_an_archived_thread_is_woken_and_a_missing_one_made():
    old = Thread(archived=True)
    lobby = Lobby(archived=[old])
    await _radio(lobby).post("line")
    assert old.archived is False and old.sent

    empty = Lobby()
    await _radio(empty).post("line")
    assert empty.created == [(THREAD_NAME, discord.ChannelType.public_thread, radio.ARCHIVE_MINUTES)]


@pytest.mark.asyncio
async def test_only_the_host_that_answers_for_main_posts():
    thread = Thread()
    assert await _radio(Lobby(threads=[thread]), Ownership(mine=False)).post("line") is None
    # Claims unknown: prod posts rather than lose the line; a dev doesn't.
    assert await _radio(Lobby(threads=[thread]), Ownership(ready=False, role="prod")).post("line") == "101"
    assert await _radio(Lobby(threads=[Thread()]), Ownership(ready=False, role="dev")).post("line") is None


@pytest.mark.asyncio
async def test_the_reason_poll_replies_to_its_line():
    thread = Thread()
    poll_id = await _radio(Lobby(threads=[thread])).poll("101", "What was wrong with #1?", ("wrong agent", "wrong fact"))
    assert poll_id == "101"
    _, kwargs = thread.sent[0]
    assert kwargs["reference"] == "ref:101"
    assert kwargs["poll"].question == "What was wrong with #1?"
    assert [a.text for a in kwargs["poll"].answers] == ["wrong agent", "wrong fact"]


@pytest.mark.asyncio
async def test_listeners_take_reactions_and_votes_in_turn():
    calls = []

    async def first(message_id, emoji):
        calls.append("first")
        return False

    async def broken(message_id, emoji):
        raise RuntimeError("boom")

    async def second(message_id, emoji):
        calls.append("second")
        return True

    try:
        for listener in (first, broken, second):
            radio.on_reaction(listener)
        assert await radio.reaction_added("1", "✅") and calls == ["first", "second"]
        assert not await radio.poll_vote_added("1", 1)
    finally:
        radio.clear_listeners()
