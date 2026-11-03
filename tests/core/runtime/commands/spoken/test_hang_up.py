"""Hang up: which utterances end the voice session, and what the command asks of the room."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.runtime.commands.base import SpokenContext
from core.runtime.commands.spoken.hang_up import HangUpCommand, is_hang_up
from core.runtime.commands.spoken.stop import bare_stop


@pytest.mark.parametrize("transcript", [
    "hanging up", "Hanging up.", "Hang up.", "hang-up",
    "I'm hanging up now.", "I’m hanging up",                 # a curly apostrophe, as some recognizers write it
    "Okay, bye, hanging up.", "Alright, I'm gonna hang up now. Bye!",
    "You can hang up now.", "Let's hang up.", "I have to hang up",
    "Thanks, hanging up. Talk to you later!", "hang up the call", "Hanging up on you, bye bye",
])
def test_ways_to_hang_up(transcript):
    assert is_hang_up(transcript)


@pytest.mark.parametrize("transcript", [
    "don't hang up",
    "Should I hang up the picture?",
    "Remind me to hang up the laundry tomorrow",
    "Why did you hang up?",
    "hang up on her",
    "hang on",                 # a hold (wait.py)
    "goodbye",                 # too easy to say mid-conversation
    "bye",
    "I'm hanging out with friends tonight",
    "stop",
    "",
])
def test_what_is_not_hanging_up(transcript):
    assert not is_hang_up(transcript)


def test_hang_up_is_never_taken_for_a_stop_or_hold():
    """"hang on" holds; "hang up" must not, though both start with "hang"."""
    assert bare_stop("hang up") is None
    assert bare_stop("hanging up") is None


@pytest.mark.asyncio
async def test_the_room_hangs_up_on_whoever_said_it():
    room, say, member = MagicMock(), AsyncMock(), MagicMock(display_name="Sam", bot=False)
    room.hang_up = AsyncMock(return_value=True)
    turn = MagicMock()
    turn.speaker.return_value = member
    command = HangUpCommand()
    ctx = SpokenContext(voice=room, say=say, turn=turn, heard="hanging up", log=lambda _line: None)
    assert await command.run(ctx, command.match("hanging up")) is True
    room.hang_up.assert_awaited_once_with(say, member)


def test_it_is_listed_with_examples_and_a_trace_outcome():
    command = HangUpCommand()
    assert command.examples and command.outcome == "hang_up"
    assert command.match("hanging up") == "" and command.match("hang the picture up") is None
