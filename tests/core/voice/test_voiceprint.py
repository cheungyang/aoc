"""Hearing only you, through the room: the voiceprint gate in shadow mode, and
`!enroll` (EnrollmentFlow, in core/voice/listening/enrollment.py)."""
import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from core.voice.foundation import settings as voice_settings
from core.voice.voice_manager import VoiceManager
from tests.core.voice.voice_room import (
    QUESTION, TEXT_OUTPUT, _capture_sessions, _final, _focused, _play_at_once, _room_fixture, _spoken,
    _spoken_reply, _streaming_fixture, _turn,
)


def _you():
    return MagicMock(id=42, display_name="Sam", bot=False)


def _reading(text="Add oat milk, eggs and spinach to the shopping list for Saturday.", ms=3000, user=None):
    """An utterance with `ms` of speech, its transcript already final."""
    from core.voice.foundation.utterance import BYTES_PER_MS
    utterance = _spoken(text, user=user or _you())
    utterance.pcm += bytes(ms * BYTES_PER_MS)
    return utterance


async def _hear(vm, utterance):
    vm.floor.utterance_opened(utterance)
    vm.floor.utterance_finished(utterance)
    await vm._current_task
    await asyncio.gather(*list(vm._side_tasks))


def _enrolling_room(mock_bot_runner, tmp_path):
    """The room, with you in the voice channel and a speaker model to enroll with."""
    from tests.helpers import FakeSpeakerModel
    vm, main_agent, loader, channels = _room_fixture(mock_bot_runner, tmp_path)
    channels["room"].members = [MagicMock(bot=False)]
    vm.speaker_gate._model = FakeSpeakerModel()
    return vm, main_agent, loader, channels


OPENING = "Let's take your voiceprint. Read the four sentences in the chat, pausing after each."


@pytest.mark.asyncio
async def test_enroll_takes_four_readings_instead_of_requests_and_saves_your_voiceprint(mock_bot_runner, tmp_path):
    vm, main_agent, loader, channels = _enrolling_room(mock_bot_runner, tmp_path)
    with _focused(loader):
        posted = await vm.enroll("", channels["general"])
        await asyncio.gather(*list(vm._side_tasks))
        for _ in range(4):
            await _hear(vm, _reading())

    assert posted.startswith("🎙️ **Voiceprint enrollment.**")
    main_agent.execute_stream.assert_not_called()
    assert vm.reply.tts.said() == [OPENING, "Got it. Three more.", "Got it. Two more.", "Got it. One more.",
                                   "Done. Your voiceprint is saved."]
    saved = vm.speaker_gate.voiceprint
    assert (saved.readings, saved.user_id) == (4, "42")
    assert vm.speaker_gate.enrollment is None
    assert any("Voiceprint saved from 4 readings" in c.args[0] for c in channels["general"].send.await_args_list)


@pytest.mark.asyncio
async def test_during_enroll_wait_is_ignored_a_few_words_are_asked_again_and_cancel_stops_it(mock_bot_runner, tmp_path):
    vm, main_agent, loader, channels = _enrolling_room(mock_bot_runner, tmp_path)
    calls = _capture_sessions(main_agent)
    with _focused(loader):
        await vm.enroll("", channels["general"])
        await asyncio.gather(*list(vm._side_tasks))
        await _hear(vm, _spoken("Hang on."))
        await _hear(vm, _spoken("Um, which one?"))
        await _hear(vm, _spoken("Cancel."))
        assert vm.speaker_gate.enrollment is None
        await _turn(vm, "What's on my calendar?")

    assert vm.reply.tts.said() == [OPENING, "I missed that. Sentence one, please.", "Okay, enrollment cancelled.",
                                   "Done."]
    assert [prompt for prompt, _session in calls] == ["What's on my calendar?"], "back to the agents"
    channels["general"].send.assert_any_await("Enrollment cancelled.")


@pytest.mark.asyncio
async def test_enroll_needs_you_in_the_voice_channel_and_the_model(mock_bot_runner, tmp_path):
    vm, _agent, loader, channels = _enrolling_room(mock_bot_runner, tmp_path)
    with _focused(loader):
        channels["room"].members = []
        assert await vm.enroll("") == "Join the voice channel first, then type `!enroll` again."
        channels["room"].members = [MagicMock(bot=False)]
        model, vm.speaker_gate._model = vm.speaker_gate._model, None
        with patch.object(voice_settings, "speaker_model_path", return_value=None):
            assert "isn't installed" in await vm.enroll("")
        assert vm.speaker_gate.enrollment is None

        vm.speaker_gate._model = model
        assert await vm.enroll("cancel") == "No enrollment is in progress."
        assert await vm.enroll("now") == "Type `!enroll` to take your voiceprint, or `!enroll cancel` to stop."
        await vm.enroll("", channels["general"])
        assert await vm.enroll("cancel") == "Enrollment cancelled."
        assert vm.speaker_gate.enrollment is None
        await asyncio.gather(*list(vm._side_tasks))


@pytest.mark.asyncio
async def test_an_enrollment_ends_with_the_voice_session_or_when_left_too_long(mock_bot_runner, tmp_path):
    from core.voice.listening.enrollment import ENROLL_TIMEOUT_S, Enrollment
    vm, main_agent, loader, channels = _enrolling_room(mock_bot_runner, tmp_path)
    calls = _capture_sessions(main_agent)
    with _focused(loader):
        now = [0.0]
        vm.speaker_gate.enrollment = Enrollment(clock=lambda: now[0])
        now[0] = ENROLL_TIMEOUT_S + 1
        await _turn(vm, "What's on my calendar?")
        assert [prompt for prompt, _session in calls] == ["What's on my calendar?"], "a turn again"
        assert vm.speaker_gate.enrollment is None

        vm.speaker_gate.enrollment = Enrollment()
        vm._on_joined(channels["room"])
        assert vm.speaker_gate.enrollment is None
        for end in (vm.welcome, vm.leave):
            vm.speaker_gate.enrollment = Enrollment()
            await end()
            assert vm.speaker_gate.enrollment is None


@pytest.mark.asyncio
async def test_each_join_reads_the_gate_settings_afresh(mock_bot_runner, tmp_path):
    vm, main_agent, loader, channels = _room_fixture(mock_bot_runner, tmp_path)
    main_agent.config = {"voice_config": {"speaker_gate": "shadow", "owner_user_ids": ["42"]}}
    with _focused(loader):
        vm._on_joined(channels["room"])
        assert vm.speaker_gate.mode == "shadow" and vm.speaker_gate.owner_ids() == {"42"}
        main_agent.config = {"voice_config": {"speaker_gate": "off"}}
        await vm.welcome()
    assert vm.speaker_gate.mode == "off" and vm.speaker_gate.owner_ids() == set()


def test_the_sink_hears_only_the_gates_owners(mock_bot_runner):
    vm = VoiceManager(mock_bot_runner, text_output=TEXT_OUTPUT)
    with patch("core.voice.voice_manager.VADSink") as sink_class:
        vm._make_sink()
    assert sink_class.call_args.kwargs["owners"] == vm.speaker_gate.owner_ids


@pytest.mark.asyncio
async def test_shadow_mode_records_each_utterance_and_changes_nothing(mock_bot_runner, tmp_path):
    from core.storage.speaker_gate_log import Effect, GateLog
    from core.voice.listening import speaker_gate as sg
    from tests.helpers import FAKE_VOICEPRINT, FakeSpeakerModel
    vm, main_agent, loader, channels = _room_fixture(mock_bot_runner, tmp_path)
    calls = _capture_sessions(main_agent)
    vm.speaker_gate._model = FakeSpeakerModel(lambda ms: 0.2)  # it would call this "not you"
    sg.Voiceprint(embedding=FAKE_VOICEPRINT, model=FakeSpeakerModel.name, enrolled_at=time.time(), readings=4,
                  consistency=0.8, user_id="42").save(sg.voiceprint_path())
    main_agent.config = {"voice_config": {"speaker_gate": "shadow"}}
    with _focused(loader):
        vm._on_joined(channels["room"])
        await _hear(vm, _reading("Turn left at the next light.", ms=1800))
        await asyncio.gather(*list(vm.speaker_gate._tasks))

    assert [prompt for prompt, _session in calls] == ["Turn left at the next light."], "shadow blocks nothing"
    (record,) = GateLog().to_review(since=0, borderline_below=2.0)
    assert (record.verdict, record.effect, record.user_id) == ("NOT_YOU", Effect.TURN, "42")
    assert record.transcript == "Turn left at the next light." and record.audio_path


@pytest.mark.asyncio
async def test_the_turn_trace_carries_each_utterances_verdict(mock_bot_runner, tmp_path):
    from core.voice.listening.speaker_gate import Assessment, Verdict
    vm, agent, _channel = _streaming_fixture(mock_bot_runner, tmp_path, [
        {"type": "token", "content": _spoken_reply("Logged it.")},
        _final(_spoken_reply("Logged it.")),
    ])
    _play_at_once(vm)
    utterance = _spoken(QUESTION)
    utterance.gate = Assessment(Verdict.YOU, 0.83, 1000, voiced_share=0.9, compute_ms=8)
    traces = []
    with patch("core.runtime.observability.turn_trace.TurnTrace.finish", autospec=True,
               side_effect=lambda self, session_id=None, surface="voice": traces.append(self)):
        with patch("core.voice.voice_manager.AgentsLoader") as mock_loader, \
             patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None):
            mock_loader.return_value.get_agent.return_value = agent
            vm.floor.utterance_opened(utterance)
            vm.floor.utterance_finished(utterance)
            await vm._current_task

    (trace,) = traces
    assert trace.meta["speaker"] == [{"verdict": "YOU", "score": 0.83, "decided_ms": 1000, "voiced": 0.9,
                                      "compute_ms": 8}]
