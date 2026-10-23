"""Switch: which utterances ask to move the voice room, and to what."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.runtime.commands.base import SpokenContext
from core.runtime.commands.spoken.switch import SwitchCommand, match_target, parse_room_request
from core.runtime.commands.spoken.words import alias_key

# Alias -> focus key, as the voice room's alias table builds it.
TARGETS = {alias_key(name.replace("-", " ")): key for key, names in [
    ("day-planning", ["day planning", "Daisy"]),
    ("fitness-coach", ["fitness coach", "Frankie"]),
    ("meal-planning", ["meal planning", "Ramsay"]),
    ("home-automation", ["home automation", "Butler"]),
    ("general", ["general", "concierge"]),
] for name in names}


@pytest.mark.parametrize("transcript, target", {
    "Take me to day planning.": "day-planning",
    "take me to Daisy's room": "day-planning",
    "Let's go to the fitness coach room": "fitness-coach",
    "Hey concierge, switch to meal planning please": "meal-planning",
    "Can you take me to Butler?": "home-automation",
    "I want to talk to Ramsay": "meal-planning",
    "Okay, go to Frankie.": "fitness-coach",
    "move me into home automation": "home-automation",
    "Switch me to Daisy.": "day-planning",
    "Take me to concierge.": "general",
    "Back to concierge.": "general",
    "Take me back to the concierge": "general",
    "go back to day planning": "day-planning",
    "Take me to general": "general",
    "Start with Daisy.": "day-planning",
    "start from meal planning": "meal-planning",
}.items())
def test_commands_that_switch(transcript, target):
    assert match_target(transcript, TARGETS) == target


@pytest.mark.parametrize("transcript", [
    "go to day planning and add a task",
    "What's on my day planning list?",
    "Talk to me about dinner",
    "take me to the airport on Friday",
    "Daisy, what's next?",
    "Back to the dentist thing: move it to Friday",
])
def test_ordinary_requests_are_not_switches(transcript):
    """A phrase that names no target goes to the agent you're with."""
    assert match_target(transcript, TARGETS) is None


@pytest.mark.parametrize("transcript", ["switch over to the day planner", "take me to the lobby",
                                        "return to the main menu"])
def test_names_only_not_agent_ids_or_old_lobby_words(transcript):
    """"the day planner" is a near miss (Daisy's id, not her name); "lobby" and
    "main menu" are gone: the lobby is the concierge."""
    assert match_target(transcript, TARGETS) is None
    assert parse_room_request(transcript) is not None


def test_parse_extracts_the_target_phrase():
    assert parse_room_request("Take me to the Day-Planning room, please!") == "day planning"
    assert parse_room_request("Switch me to Daisy.") == "daisy"
    assert parse_room_request("take me to exercise") == "exercise"
    assert parse_room_request("Turn off the kitchen lights") is None


def test_filler_words_are_ignored_on_both_sides():
    """"the concierge" is said with "the" and stored without: both must meet."""
    assert match_target("take me to the concierge", {"concierge": "general"}) == "general"


@pytest.mark.asyncio
async def test_the_room_does_the_moving_and_may_decline():
    room, say = MagicMock(), AsyncMock()
    room.switch_to_named = AsyncMock(return_value=False)
    command = SwitchCommand()
    phrase = command.match("Take me to the airport on Friday")
    assert await command.run(SpokenContext(voice=room, say=say), phrase) is False
    room.switch_to_named.assert_awaited_once_with("airport on friday", say)
