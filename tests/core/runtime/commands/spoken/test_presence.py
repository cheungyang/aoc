"""Presence: "hello?", "are you there?" -- you checking the room hears you,
which on joining means its greeting never reached you."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.runtime.commands.base import SpokenContext
from core.runtime.commands.registry import SPOKEN
from core.runtime.commands.spoken.presence import PresenceCommand, is_presence_check
from core.runtime.observability.turn_trace import Outcome


@pytest.mark.parametrize("transcript", [
    "Hello.", "hello?", "Hello? Hello?", "Hi there", "Hey", "um, hello?", "Okay, hello?",
    "Are you there?", "Hello, are you there?", "Is anyone there?", "Anybody there?",
    "Can you hear me?", "Can you hear me now?", "Do you hear me", "Testing, testing.",
])
def test_ways_to_check_you_are_heard(transcript):
    assert is_presence_check(transcript)


@pytest.mark.parametrize("transcript", [
    "Okay.",                                # an answer
    "So",                                   # a filler on its own
    "Hey Daisy",                            # a name: a switch (switch.py)
    "Daisy, are you there?",
    "Hello, what's on my list today?",      # a request that opens politely
    "Are you there yet with the summary?",
    "I can hear you",
    "",
])
def test_what_is_not_a_presence_check(transcript):
    assert not is_presence_check(transcript)


@pytest.mark.asyncio
async def test_the_room_says_whether_it_answered():
    """True: the greeting is replayed or on its way. False: the session is
    under way, and "hello?" is words for the agent you're with."""
    room, say = MagicMock(), AsyncMock()
    room.presence = AsyncMock(return_value=False)
    command = PresenceCommand()
    assert await command.run(SpokenContext(voice=room, say=say), command.match("hello?")) is False
    room.presence.assert_awaited_once_with(say)


@pytest.mark.asyncio
@pytest.mark.parametrize("answered", [True, False], ids=["before-anything-else", "session-under-way"])
async def test_in_the_registry_it_answers_only_what_the_room_answers(answered):
    room = MagicMock()
    room.presence = AsyncMock(return_value=answered)
    room.switch_to_named = AsyncMock(return_value=None)  # "hello" names no one
    ctx = SpokenContext(voice=room, heard="Hello?", say=AsyncMock(), turn=MagicMock())
    command = await SPOKEN.answer("Hello?", ctx)
    if answered:
        assert isinstance(command, PresenceCommand) and command.outcome == Outcome.GREETING
    else:
        assert command is None, "the words go to the agent you're with"
