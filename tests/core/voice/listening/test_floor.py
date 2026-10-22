"""Floor: every finished utterance goes to the voiceprint gate, with what it did.

The rest of the floor -- barge-in, backchannels, unanswered words -- is tested
through VoiceManager (tests/core/voice/test_voice_manager.py).
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.voice.listening.floor import Floor
from core.voice.listening.gate_log import Effect
from core.voice.listening.utterance import Transcript, Utterance


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
