"""Go back: which utterances ask to undo the room's last move."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.runtime.commands.base import SpokenContext
from core.runtime.commands.spoken.go_back import GoBackCommand, is_go_back


@pytest.mark.parametrize("transcript", [
    "go back", "Go back.", "No, go back", "Take me back", "bring me back please", "switch back",
    "Back.", "Undo that", "Wrong agent", "Oops, wrong one", "previous agent",
])
def test_ways_to_ask_to_go_back(transcript):
    assert is_go_back(transcript)


@pytest.mark.parametrize("transcript", [
    "Go back to Grace",                       # a switch phrase (switch.py)
    "Let's go back to milestone one",
    "go back to the first option",
    "Back to the dentist thing: move it to Friday",
    "never mind",                             # a stop phrase
])
def test_what_is_not_going_back(transcript):
    assert not is_go_back(transcript)


@pytest.mark.asyncio
async def test_the_room_says_whether_there_was_somewhere_to_go_back_to():
    room, say = MagicMock(), AsyncMock()
    room.go_back = AsyncMock(return_value=False)
    command = GoBackCommand()
    assert await command.run(SpokenContext(voice=room, say=say), command.match("go back")) is False
    room.go_back.assert_awaited_once_with(say)
