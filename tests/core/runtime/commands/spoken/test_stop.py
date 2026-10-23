"""Stop and wait: "stop" or "wait" on its own, and the phrases that interrupt."""
from unittest.mock import MagicMock

import pytest

from core.runtime.commands.base import SpokenContext
from core.runtime.commands.spoken.stop import StopCommand, bare_stop, is_stop_request
from core.runtime.commands.spoken.wait import WaitCommand
from core.runtime.observability.turn_trace import Outcome


@pytest.mark.parametrize("text", ["stop", "Wait", "hold on", "Never mind", "okay stop"])
def test_a_stop_phrase_is_a_stop_request_however_short(text):
    assert is_stop_request(text)


def test_a_stop_phrase_late_in_a_sentence_is_not_a_stop_request():
    assert not is_stop_request("I want to know when the bus will stop")


class TestBareStop:
    @pytest.mark.parametrize("text", ["Stop.", "Stop, stop.", "Okay, stop talking.", "Never mind.",
                                      "Cancel that.", "No, no.", "Forget it.", "Enough."])
    def test_stop_drops_the_request(self, text):
        assert bare_stop(text) == "cancel"

    @pytest.mark.parametrize("text", ["Wait.", "Hold on.", "Wait a second.", "Hang on a sec.",
                                      "Hold up, please."])
    def test_wait_keeps_the_request(self, text):
        assert bare_stop(text) == "hold"

    @pytest.mark.parametrize("text", ["Wait, make it Friday.", "Stop the timer.", "Wait, what?",
                                      "Hold on, what about Thursday?", "Okay.", ""])
    def test_anything_more_is_a_request(self, text):
        assert bare_stop(text) is None


def test_stop_and_wait_each_hear_their_own():
    stop, wait = StopCommand(), WaitCommand()
    assert (stop.match("Never mind."), wait.match("Never mind.")) == ("", None)
    assert (stop.match("Hold on."), wait.match("Hold on.")) == (None, "")
    assert (stop.match("Wait, make it Friday."), wait.match("Wait, make it Friday.")) == (None, None)
    assert (stop.outcome, wait.outcome) == (Outcome.STOP_CANCEL, Outcome.STOP_HOLD)


@pytest.mark.asyncio
async def test_stop_answers_and_wait_holds_the_turn():
    turn, logged = MagicMock(), []
    assert await StopCommand().run(SpokenContext(heard="Stop.", turn=turn, log=logged.append), "") is True
    turn.hold.assert_not_called()
    assert await WaitCommand().run(SpokenContext(heard="Wait.", turn=turn, log=logged.append), "") is True
    turn.hold.assert_called_once()
    assert logged == ["✋ Heard 'Stop.': stopping, request dropped.",
                      "✋ Heard 'Wait.': stopping, keeping the request for what comes next."]
