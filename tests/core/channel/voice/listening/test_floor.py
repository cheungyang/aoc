"""Floor: every finished utterance goes to the voiceprint gate, with what it did.

The rest of the floor -- barge-in, backchannels, unanswered words -- is tested
through VoiceManager (tests/core/channel/voice/test_voice_manager.py).
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.storage.speaker_gate_log import Effect
from core.channel.voice.listening.floor import Floor
from core.channel.voice.foundation.utterance import Transcript, Utterance


class RecordingGate:
    def __init__(self, fails=False):
        self.observed = []
        self.fails = fails

    def observe(self, utterance, effect):
        self.observed.append((utterance, effect))
        if self.fails:
            raise RuntimeError("gate broke")


class Turns:
    def __init__(self, busy=False):
        self.is_busy, self.began, self.interrupted = busy, 0, []

    def busy(self):
        return self.is_busy

    def begin_turn(self):
        self.began += 1

    def interrupt(self, reason):
        self.interrupted.append(reason)


def make_floor(busy=False, gate=None):
    stt = MagicMock()
    stt.transcribe = AsyncMock(return_value=Transcript("Okay.", "gemini_live"))
    stt.echo.return_value = None  # words are words here; echoes are test_voice_manager.py's
    audio = MagicMock()
    audio.is_playing.return_value = busy
    connection = MagicMock(agent_id="main")
    connection.own_user.return_value = None
    turns = Turns(busy)
    return Floor(stt, audio, connection, turns, gate=gate if gate is not None else RecordingGate()), turns


def said(floor, confirmed=False):
    utterance = Utterance(user=MagicMock(id=7, bot=False))
    floor.utterance_opened(utterance)
    utterance.confirmed = confirmed
    floor.utterance_finished(utterance)
    return utterance


def test_an_utterance_to_an_idle_agent_starts_a_turn():
    floor, turns = make_floor()
    utterance = said(floor)
    assert floor.gate.observed == [(utterance, Effect.TURN)]
    assert turns.began == 1


def test_one_that_joins_waiting_words_is_a_merge():
    floor, _ = make_floor()
    floor._pending = [Utterance()]
    utterance = said(floor)
    assert floor.gate.observed == [(utterance, Effect.MERGE)]


def test_expired_waiting_words_dont_make_a_merge():
    floor, _ = make_floor()
    old = Utterance()
    old.started_at -= 10_000
    floor._pending = [old]
    said(floor)
    assert floor.gate.observed[0][1] is Effect.TURN


@pytest.mark.asyncio
async def test_over_a_busy_agent_its_a_barge_in_or_an_overlap():
    floor, turns = make_floor(busy=True)
    confirmed = said(floor, confirmed=True)
    unconfirmed = said(floor)
    await asyncio.gather(*list(floor._overlap_tasks))
    assert floor.gate.observed == [(confirmed, Effect.BARGE_IN), (unconfirmed, Effect.OVERLAP)]


def test_a_discarded_blip_is_not_scored():
    floor, _ = make_floor()
    utterance = Utterance()
    floor.utterance_opened(utterance)
    floor.utterance_discarded(utterance)
    assert floor.gate.observed == []


def test_a_broken_gate_never_holds_up_the_floor(capsys):
    floor, turns = make_floor(gate=RecordingGate(fails=True))
    said(floor)
    assert turns.began == 1
    assert "voiceprint gate failed" in capsys.readouterr().out


def test_no_gate_is_fine():
    floor, turns = make_floor()
    floor.gate = None
    said(floor)
    assert turns.began == 1


# --- over the room's greeting -------------------------------------------------------------

class GreetingTurns(Turns):
    """The room's greeting is what's playing."""

    def __init__(self, playing=True):
        super().__init__(busy=True)
        self.playing = playing

    def greeting_playing(self):
        return self.playing


def _over(text, playing=True):
    """`text` is being said over the greeting (or, not `playing`, over a reply)."""
    floor, _ = make_floor(busy=True)
    floor.turns = turns = GreetingTurns(playing)
    floor.stt.transcribe = AsyncMock(return_value=Transcript(text, "gemini_live"))
    utterance = Utterance(user=MagicMock(id=7, bot=False))
    floor.utterance_opened(utterance)
    utterance.interim = text
    floor._on_text(utterance)
    return floor, turns, utterance


def test_two_words_over_the_greeting_wait_for_the_end_of_what_you_say():
    """\"Are you there?\" is two words: over a reply that stops it at once."""
    _floor, turns, _ = _over("are you there")
    assert turns.interrupted == []
    _floor, turns, _ = _over("are you there", playing=False)
    assert turns.interrupted == ["heard 'are you there'"]


def test_a_stop_phrase_stops_the_greeting_at_once():
    _floor, turns, _ = _over("stop")
    assert turns.interrupted == ["heard 'stop'"]


@pytest.mark.asyncio
async def test_hello_over_the_greeting_lets_it_play_on():
    floor, turns, utterance = _over("Hello? Are you there?")
    floor.utterance_finished(utterance)
    await asyncio.gather(*list(floor._overlap_tasks))
    assert turns.interrupted == [] and turns.began == 0
    assert floor.unanswered() == []


@pytest.mark.asyncio
async def test_a_request_over_the_greeting_is_a_turn_once_said():
    floor, turns, utterance = _over("Switch me to Daisy")
    assert turns.interrupted == []
    floor.utterance_finished(utterance)
    await asyncio.gather(*list(floor._overlap_tasks))
    assert turns.interrupted == ["heard 'Switch me to Daisy'"] and turns.began == 1
