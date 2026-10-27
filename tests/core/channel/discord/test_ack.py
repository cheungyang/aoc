"""The `now` reaction's Discord side (core/channel/discord/ack.py), against stand-ins for discord.py."""
from types import SimpleNamespace

import pytest

from core.channel.discord.ack import DiscordAck


class Channel:
    """A channel as one bot sees it: the reactions put on its messages, and by which bot."""

    def __init__(self, channel_id, by, fails=None):
        self.id, self.by, self.fails, self.reactions = channel_id, by, fails, []

    def get_partial_message(self, message_id):
        channel = self

        class Message:
            async def add_reaction(self, emoji):
                if channel.fails:
                    raise channel.fails
                channel.reactions.append((channel.by, message_id, emoji))

        return Message()


class Client:
    """A bot's discord client: whether it's connected, and the channels it sees."""

    def __init__(self, channels=(), ready=True):
        self.ready, self.channels = ready, {channel.id: channel for channel in channels}

    def is_ready(self):
        return self.ready

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)


class Broken(Client):
    def get_channel(self, channel_id):
        raise RuntimeError("cache gone")


class Bots:
    """BotsLoader, stood in for: the bots this host runs, by agent."""

    def __init__(self, clients):
        self.clients = clients

    def get_bot(self, agent_id):
        client = self.clients.get(agent_id)
        return SimpleNamespace(bot=client) if client is not None else None


@pytest.mark.asyncio
async def test_the_recipients_own_bot_reacts_when_it_runs_here_and_sees_the_channel():
    ramsays, mains = Channel(111, "meal-planner"), Channel(111, "main")
    bots = Bots({"meal-planner": Client([ramsays]), "main": Client([mains])})
    assert await DiscordAck(bots).react("111/222", "🧑‍🍳", "meal-planner")
    assert ramsays.reactions == [("meal-planner", 222, "🧑‍🍳")] and mains.reactions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("why, ramsays", [
    ("its bot doesn't run here", None),
    ("its bot isn't connected", Client([Channel(111, "meal-planner")], ready=False)),
    ("its bot doesn't see the channel", Client()),
    ("its bot can't look the channel up", Broken()),
])
async def test_else_mains_bot_reacts(why, ramsays):
    mains = Channel(111, "main")
    clients = {"main": Client([mains])}
    if ramsays is not None:
        clients["meal-planner"] = ramsays
    assert await DiscordAck(Bots(clients)).react("111/222", "🧑‍🍳", "meal-planner"), why
    assert mains.reactions == [("main", 222, "🧑‍🍳")]


@pytest.mark.asyncio
async def test_a_reaction_that_cant_be_made_is_logged_never_raised(capsys):
    assert not await DiscordAck(Bots({"main": Client()})).react("111/222", "🧑‍🍳", "meal-planner")
    refused = Bots({"main": Client([Channel(111, "main", fails=RuntimeError("Missing Access"))])})
    assert not await DiscordAck(refused).react("111/222", "🧑‍🍳", "meal-planner")
    assert not await DiscordAck(refused).react("not a message", "🧑‍🍳", "meal-planner")
    out = capsys.readouterr().out
    assert "no bot here sees channel 111" in out and "Missing Access" in out
