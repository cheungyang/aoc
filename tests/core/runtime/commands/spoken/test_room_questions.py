"""Where am I, and next agent: their phrases, and what they ask of the room."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.runtime.commands.base import SpokenContext
from core.runtime.commands.spoken.next_agent import NextAgentCommand, is_next_agent
from core.runtime.commands.spoken.where_am_i import WhereAmICommand, is_where_am_i


@pytest.mark.parametrize("transcript", ["Where am I?", "which channel am I in", "Hey concierge, where am I right now?"])
def test_questions_about_the_place(transcript):
    assert is_where_am_i(transcript)


@pytest.mark.parametrize("transcript", ["who am I talking to", "Who is this?", "Who are you?"])
def test_who_questions_go_to_the_agent(transcript):
    """"Who are you?" is answered by the agent you're with, in its own words."""
    assert not is_where_am_i(transcript)


@pytest.mark.parametrize("transcript", ["Where am I going tomorrow?", "Who is on my calendar today?",
                                        "where are my keys"])
def test_other_questions_are_not(transcript):
    assert not is_where_am_i(transcript)


@pytest.mark.asyncio
async def test_where_am_i_is_said_by_the_agent_you_are_with():
    room, say = MagicMock(), AsyncMock()
    room.whereabouts.return_value = ("You're in day planning, with Daisy.", "day-planner")
    assert await WhereAmICommand().run(SpokenContext(voice=room, say=say), "") is True
    say.assert_awaited_once_with("You're in day planning, with Daisy.", "day-planner")


@pytest.mark.parametrize("transcript", ["Next agent.", "next agent please", "Okay, next agent", "go to the next agent",
                                        "take me to the next agent", "Move on to the next agent", "skip to next agent"])
def test_next_agent_as_a_whole_utterance(transcript):
    assert is_next_agent(transcript)


@pytest.mark.parametrize("transcript", ["Who is the next agent?", "next agent meeting is at noon", "next",
                                        "the next one", "add a task for the next agent"])
def test_next_agent_not_inside_a_request(transcript):
    assert not is_next_agent(transcript)


@pytest.mark.asyncio
async def test_next_agent_asks_the_room_which_may_have_no_feature_for_it():
    room, say = MagicMock(), AsyncMock()
    for answered in (True, False):
        room.next_agent = AsyncMock(return_value=answered)
        assert await NextAgentCommand().run(SpokenContext(voice=room, say=say), "") is answered
        room.next_agent.assert_awaited_once_with(say)
