"""Switch: which utterances ask to move the voice room, to what, and how they
asked -- a switch phrase, switch words, or a bare name. Whether the target is
a name the room knows is the room's (tests/core/voice/room/test_room_switch.py).
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.runtime.commands.base import SpokenContext
from core.runtime.commands.spoken.switch import SwitchCommand, SwitchRequest, match_target, parse_switch
from core.runtime.commands.spoken.words import alias_key
from core.runtime.observability.turn_trace import Outcome

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
    "Daisy.": "day-planning",
    "Can I talk to Frankie please": "fitness-coach",
    "Switch to Frankie again": "fitness-coach",
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
    assert parse_switch(transcript).asked


def test_parse_extracts_the_target_phrase():
    assert parse_switch("Take me to the Day-Planning room, please!").phrase == "day planning"
    assert parse_switch("Switch me to Daisy.").phrase == "daisy"
    assert parse_switch("take me to exercise").phrase == "exercise"
    assert parse_switch("Turn off the kitchen lights") is None


def test_filler_words_are_ignored_on_both_sides():
    """"the concierge" is said with "the" and stored without: both must meet."""
    assert match_target("take me to the concierge", {"concierge": "general"}) == "general"


@pytest.mark.parametrize("transcript, phrase", {
    "Switch me to Daisy.": "daisy",
    "take me to the market scout": "market scout",
    "Can I talk to Grace please": "grace",
    "Could I speak with Frankie": "frankie",
    "I'd like to talk to Ramsay": "ramsay",
    "connect me to Butler": "butler",
    "Take me to exercise": "exercise",   # a near miss is worth a model call
}.items())
def test_a_switch_phrase_asks(transcript, phrase):
    assert parse_switch(transcript) == SwitchRequest(phrase, asked=True, bare=False)


@pytest.mark.parametrize("transcript, phrase", {
    # From the prod voice log: no switch phrase leads these.
    "Let's start with Grace.": "grace",
    "Start with Marvin.": "marvin",
    "Chat with Marvin.": "marvin",
    "I'm with Marvin.": "marvin",
    "With Daisy.": "daisy",
    "Over to Grace": "grace",
    "Get me Daisy": "daisy",
    "Let's talk to Daisy": "daisy",
    # An answer, in the logs, not a switch: the room finds no one by that
    # name, and with no switch phrase there's no model call to find one.
    "Let's start with Michael.": "michael",
}.items())
def test_switch_words_without_a_switch_phrase_are_neither_asked_nor_bare(transcript, phrase):
    assert parse_switch(transcript) == SwitchRequest(phrase, asked=False, bare=False)


@pytest.mark.parametrize("transcript, phrase", {
    # From the prod voice log, each of which failed before.
    "Grace.": "grace",
    "Grace. Grace.": "grace",
    "Daisy.": "daisy",
    "Frankie.": "frankie",
    # Openers, politeness and a presence check leave a name bare.
    "Hey Grace": "grace",
    "Grace, are you there?": "grace",
    "Thanks, Grace.": "grace",
    "Grace please": "grace",
    "Receipt Processor.": "receipt processor",
    "Rio de Janeiro": "rio de janeiro",   # reads as a three-word name; the room knows it isn't one
}.items())
def test_a_name_on_its_own_is_bare(transcript, phrase):
    assert parse_switch(transcript) == SwitchRequest(phrase, asked=False, bare=True)


@pytest.mark.parametrize("transcript", [
    "Grace, continue our conversation.",          # a request: the router hands it to Grace
    "not Grace",
    "Don't get Grace",
    "Okay.",
    "Thanks.",
    "",
    "can we please go and talk to Grace right now",   # too long for a name said without a switch phrase
])
def test_requests_and_words_with_no_name_left_are_no_switch(transcript):
    assert parse_switch(transcript) is None


def test_a_switch_phrase_s_target_loses_trailing_politeness():
    """"again", "now", "please" after the name ask for no one else."""
    assert parse_switch("switch to Grace again please").phrase == "grace"
    assert parse_switch("talk to you") is None   # nothing left but politeness


@pytest.mark.asyncio
async def test_the_room_does_the_moving_and_may_decline():
    room, say = MagicMock(), AsyncMock()
    room.switch_to_named = AsyncMock(return_value=None)
    command, turn = SwitchCommand(), MagicMock()
    ctx = SpokenContext(voice=room, heard="Take me to the airport on Friday", say=say, turn=turn)
    assert await command.run(ctx, command.match(ctx.heard)) is False
    room.switch_to_named.assert_awaited_once_with("airport on friday", say, asked=True, bare=False, turn=turn)
    assert ctx.outcome is None


@pytest.mark.asyncio
async def test_how_it_was_said_reaches_the_room_and_the_outcome_is_recorded():
    for heard, asked, bare in (("Switch me to Grace", True, False), ("Let's start with Grace.", False, False),
                               ("Grace.", False, True)):
        room, say, turn = MagicMock(), AsyncMock(), MagicMock()
        room.switch_to_named = AsyncMock(return_value=Outcome.FOCUS_SWITCH)
        command = SwitchCommand()
        ctx = SpokenContext(voice=room, heard=heard, say=say, turn=turn)
        assert await command.run(ctx, command.match(heard)) is True
        room.switch_to_named.assert_awaited_once_with("grace", say, asked=asked, bare=bare, turn=turn)
        assert ctx.outcome == Outcome.FOCUS_SWITCH
        turn.hold.assert_not_called()


@pytest.mark.asyncio
async def test_noise_drops_only_this_utterance():
    room = MagicMock()
    room.switch_to_named = AsyncMock(return_value=Outcome.NO_TRANSCRIPT)
    command, turn = SwitchCommand(), MagicMock()
    ctx = SpokenContext(voice=room, heard="Receipt Processor.", say=AsyncMock(), turn=turn)
    assert await command.run(ctx, command.match(ctx.heard)) is True
    assert ctx.outcome == Outcome.NO_TRANSCRIPT
    turn.hold.assert_called_once_with()


@pytest.mark.asyncio
async def test_without_the_words_heard_the_phrase_is_asked_for():
    """What "switch to <phrase>" has always meant."""
    room = MagicMock()
    room.switch_to_named = AsyncMock(return_value=Outcome.FOCUS_SWITCH)
    await SwitchCommand().run(SpokenContext(voice=room, say=AsyncMock()), "grace")
    assert room.switch_to_named.await_args.kwargs["asked"] is True
    assert room.switch_to_named.await_args.kwargs["bare"] is False
