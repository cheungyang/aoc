"""VADSink: turns a speaker's audio into streamed utterances with an adaptive end of turn.

Silero is replaced by a scripted probability per 36 ms window, so each test
states exactly how much speech and silence the sink hears. Every 20 ms
Discord frame yields 320 samples at 16 kHz, so a write processes at most one
576-sample window, and `feed` writes frames until the requested number of
windows has been processed.
"""
import asyncio
import time
from collections import deque
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from core.voice import settings
from core.voice.listening.utterance import Utterance
from core.voice.listening.vad_sink import WINDOW_MS, WINDOW_SAMPLES, UserVADState, VADSink

WINDOW_BYTES = WINDOW_SAMPLES * 2
# 20 ms of 48 kHz stereo at a level that passes the energy gate.
FRAME = (np.ones(1920, dtype=np.int16) * 3000).tobytes()
USER = MagicMock(id=7, display_name="Sam", bot=False)


class ScriptedVAD:
    """Stands in for the Silero ONNX session."""

    def __init__(self):
        self.upcoming = deque()
        self.calls = 0

    def run(self, _output_names, feed):
        self.calls += 1
        prob = self.upcoming.popleft() if self.upcoming else 0.0
        return np.array([prob], dtype=np.float32), feed["h"], feed["c"]


class RecordingFloor:
    """Records the utterance hooks the sink posts, in order."""

    def __init__(self):
        self.events = []

    def utterance_opened(self, u):
        self.events.append(("opened", u, 0))

    def utterance_audio(self, u, pcm):
        self.events.append(("audio", u, len(pcm)))

    def utterance_paused(self, u):
        self.events.append(("paused", u, 0))

    def utterance_resumed(self, u):
        self.events.append(("resumed", u, 0))

    def utterance_finished(self, u):
        self.events.append(("finished", u, 0))

    def utterance_discarded(self, u):
        self.events.append(("discarded", u, 0))

    def kinds(self):
        return [kind for kind, _, _ in self.events if kind != "audio"]

    def utterances(self):
        return [u for kind, u, _ in self.events if kind == "opened"]

    def audio_bytes(self):
        return sum(n for kind, _, n in self.events if kind == "audio")


@pytest.fixture
def vad():
    return ScriptedVAD()


@pytest.fixture
def make_sink(vad):
    """Builds sinks on the running loop. The watchdog is off unless asked for,
    so wall-clock time can't end a turn in the write-path tests."""
    sinks = []

    def make(watchdog=False, **kwargs):
        floor = RecordingFloor()
        with patch("onnxruntime.InferenceSession", return_value=vad):
            sink = VADSink(floor, loop=asyncio.get_running_loop(), **kwargs)
        if not watchdog:
            sink._watchdog_task.cancel()
        sinks.append(sink)
        return sink, floor

    yield make
    for sink in sinks:
        sink.cleanup()


def feed(sink, vad, user=USER, speech=0, silence=0):
    vad.upcoming.extend([0.9] * speech + [0.05] * silence)
    target = vad.calls + speech + silence
    while vad.calls < target:
        sink.write(user, MagicMock(pcm=FRAME))


async def drain():
    for _ in range(3):
        await asyncio.sleep(0)


def windows(ms):
    """Silence windows needed to reach `ms`."""
    return -(-ms // WINDOW_MS)


# --- state ----------------------------------------------------------------------

def test_user_vad_state_reset():
    state = UserVADState()
    state.utterance = Utterance()
    state.is_speaking = state.paused = True
    state.silence_chunks, state.speech_chunks, state.last_voiced_at = 10, 5, 1.0
    state.pre_speech_chunks.append(b"x")

    state.reset_speech()
    assert state.utterance is None
    assert not state.is_speaking and not state.paused
    assert (state.silence_chunks, state.speech_chunks, state.last_voiced_at) == (0, 0, None)
    assert len(state.pre_speech_chunks) == 0


def test_vad_sink_wants_opus():
    with patch("onnxruntime.InferenceSession"):
        assert VADSink(RecordingFloor()).wants_opus() is False


def test_the_agents_voice_config_sets_the_thresholds():
    config = {"vad_min_speech_ms": 400, "vad_onset_ms": 90, "vad_speech_prob": 0.8, "vad_continue_prob": 0.5}
    with patch("onnxruntime.InferenceSession"):
        sink = VADSink(RecordingFloor(), config=config)
        default = VADSink(RecordingFloor())
    assert (sink.min_speech_ms, sink.onset_ms, sink.onset_speech_prob, sink.continue_prob) == (400, 90, 0.8, 0.5)
    assert (default.min_speech_ms, default.onset_ms) == (250, 180)


def test_write_ignores_empty_frames():
    """write() returns before touching any state on empty PCM: no user keyed,
    and the VAD never invoked."""
    session = MagicMock()
    with patch("onnxruntime.InferenceSession", return_value=session):
        sink = VADSink(RecordingFloor())
    sink.write(None, MagicMock(pcm=b""))
    sink.write(USER, MagicMock(pcm=b""))
    sink.write(USER, MagicMock(pcm=None))
    assert sink.user_states == {}
    session.run.assert_not_called()

    # Control: a non-empty frame does key the speaker (4 bytes = 1 stereo
    # sample: too short to reach the VAD), so the above is the early return.
    sink.write(USER, MagicMock(pcm=b"\x00\x00\x00\x00"))
    assert list(sink.user_states) == [7]
    assert sink.user_states[7].utterance is None
    session.run.assert_not_called()


def test_bot_audio_is_ignored():
    with patch("onnxruntime.InferenceSession"):
        sink = VADSink(RecordingFloor())
    sink.write(MagicMock(id=9, bot=True), MagicMock(pcm=FRAME))
    assert sink.user_states == {}


# --- the write path --------------------------------------------------------------

@pytest.mark.asyncio
async def test_speech_then_silence_is_one_utterance_streamed_as_it_is_heard(make_sink, vad):
    sink, floor = make_sink()
    no_words = settings.END_OF_TURN_DEFAULT_MS
    feed(sink, vad, speech=10, silence=windows(no_words) - 1)
    await drain()
    # The utterance stays open without mid-sentence ACTIVITY_END until the turn ends.
    assert floor.kinds() == ["opened"]

    feed(sink, vad, silence=1)
    await drain()
    assert floor.kinds() == ["opened", "finished"]
    utterance = floor.utterances()[0]
    # 4 windows of pre-roll before the onset was confirmed, 6 more of speech,
    # then the silence up to the end of the turn.
    assert len(utterance.pcm) == (4 + 6 + windows(no_words)) * WINDOW_BYTES
    assert floor.audio_bytes() == len(utterance.pcm), "the transcriber hears exactly the utterance"
    assert utterance.speech_end_at is not None


@pytest.mark.asyncio
async def test_mid_sentence_pauses_do_not_finalize_the_segment_early_by_default(make_sink, vad):
    sink, floor = make_sink()
    feed(sink, vad, speech=10, silence=15)  # ~540 ms pause while collecting thoughts
    await drain()
    assert floor.kinds() == ["opened"]


@pytest.mark.asyncio
async def test_the_segment_is_finalized_early_when_configured(make_sink, vad):
    sink, floor = make_sink(config={"vad_early_end_ms": 200})
    feed(sink, vad, speech=10, silence=windows(200) - 1)
    await drain()
    assert floor.kinds() == ["opened"]
    feed(sink, vad, silence=1)
    await drain()
    assert floor.kinds() == ["opened", "paused"]


@pytest.mark.asyncio
async def test_a_complete_sentence_ends_the_turn_sooner(make_sink, vad):
    sink, floor = make_sink()
    feed(sink, vad, speech=10, silence=1)
    await drain()
    floor.utterances()[0].segments.append("Add milk to the shopping list.")  # the live final

    complete = windows(settings.END_OF_TURN_COMPLETE_MS)
    feed(sink, vad, silence=complete - 2)
    await drain()
    assert "finished" not in floor.kinds()
    feed(sink, vad, silence=1)
    await drain()
    assert floor.kinds()[-1] == "finished"
    assert complete * WINDOW_MS <= settings.END_OF_TURN_DEFAULT_MS


@pytest.mark.asyncio
async def test_a_trailing_conjunction_waits_longer(make_sink, vad):
    sink, floor = make_sink()
    feed(sink, vad, speech=10, silence=1)
    await drain()
    floor.utterances()[0].interim = "Add milk and"

    continuing = windows(settings.END_OF_TURN_CONTINUING_MS)
    feed(sink, vad, silence=continuing - 2)
    await drain()
    assert "finished" not in floor.kinds(), "ended mid-sentence at the default wait"
    feed(sink, vad, silence=1)
    await drain()
    assert floor.kinds()[-1] == "finished"


@pytest.mark.asyncio
async def test_talking_again_before_the_turn_ends_keeps_one_utterance(make_sink, vad):
    """A ~540 ms mid-sentence pause resumes the same utterance instead of
    splitting it into two agent turns or ending the live segment early."""
    sink, floor = make_sink()
    feed(sink, vad, speech=10, silence=15)
    feed(sink, vad, speech=10, silence=windows(settings.END_OF_TURN_DEFAULT_MS))
    await drain()
    assert floor.kinds() == ["opened", "finished"]
    assert len({id(u) for _, u, _ in floor.events}) == 1


@pytest.mark.asyncio
async def test_a_new_utterance_starts_after_a_finished_one(make_sink, vad):
    sink, floor = make_sink()
    feed(sink, vad, speech=10, silence=windows(settings.END_OF_TURN_DEFAULT_MS))
    feed(sink, vad, speech=10)
    await drain()
    assert floor.kinds() == ["opened", "finished", "opened"]
    first, second = floor.utterances()
    assert first is not second


@pytest.mark.asyncio
async def test_consonant_dip_during_onset_does_not_reset_or_clip_leading_words(make_sink, vad):
    """Once the first window crosses onset_speech_prob (0.65), consonant transitions
    above continue_prob (0.35) must not reset speech_chunks and delay onset."""
    sink, floor = make_sink()
    # Window 1 crosses 0.65; windows 2-5 dip to 0.45 on a consonant but stay >= 0.35.
    vad.upcoming.extend([0.80, 0.85, 0.45, 0.80, 0.85])
    while vad.calls < 5:
        sink.write(USER, MagicMock(pcm=FRAME))
    await drain()
    assert floor.kinds() == ["opened"]
    utterance = floor.utterances()[0]
    assert len(utterance.pcm) == 5 * WINDOW_BYTES


@pytest.mark.asyncio
async def test_soft_lead_in_before_onset_is_kept_in_preroll(make_sink, vad):
    """Soft sentence starts (>360 ms before the 180 ms onset run) stay in pre_speech_chunks."""
    sink, floor = make_sink()
    # 12 windows (432 ms) of soft lead-in below onset_speech_prob, then 5 windows of onset.
    vad.upcoming.extend([0.25] * 12 + [0.90] * 5)
    while vad.calls < 17:
        sink.write(USER, MagicMock(pcm=FRAME))
    await drain()
    assert floor.kinds() == ["opened"]
    utterance = floor.utterances()[0]
    assert len(utterance.pcm) == 17 * WINDOW_BYTES


@pytest.mark.asyncio
async def test_stale_preroll_before_a_packet_gap_is_not_prepended_to_new_speech(make_sink, vad):
    """When Discord stops sending RTP packets during silence, old pre-roll frames from
    before the gap are flushed when packets resume."""
    sink, floor = make_sink()
    feed(sink, vad, silence=8)
    state = sink.user_states[7]
    assert len(state.pre_speech_chunks) == 8
    state.last_packet_time = time.monotonic() - 1.0  # > PACKET_GAP_RESET_S

    feed(sink, vad, speech=5)
    await drain()
    assert floor.kinds() == ["opened"]
    utterance = floor.utterances()[0]
    assert len(utterance.pcm) == 5 * WINDOW_BYTES


# --- the watchdog: Discord stops sending packets in silence ------------------------

@pytest.mark.asyncio
async def test_the_watchdog_ends_the_turn_when_packets_stop(make_sink, vad):
    sink, floor = make_sink(watchdog=True)
    feed(sink, vad, speech=10)
    sink.user_states[7].last_voiced_at = time.monotonic() - 2
    await asyncio.sleep(0.2)
    assert floor.kinds() == ["opened", "finished"]


@pytest.mark.asyncio
async def test_the_watchdog_counts_from_the_last_voiced_frame_not_the_last_packet(make_sink, vad):
    """Discord sends silence frames for a while after speech stops; counting
    from those delayed every end of turn."""
    sink, floor = make_sink(watchdog=True)
    feed(sink, vad, speech=10, silence=3)  # packets still arriving
    state = sink.user_states[7]
    state.last_voiced_at = time.monotonic() - 1.0
    assert time.monotonic() - state.last_packet_time < 0.5
    await asyncio.sleep(0.2)
    assert floor.kinds()[-1] == "finished"


@pytest.mark.asyncio
async def test_a_blip_too_short_to_be_speech_is_discarded(make_sink, vad):
    sink, floor = make_sink(watchdog=True)
    feed(sink, vad, speech=5)  # onset confirmed at 180 ms, then nothing
    sink.user_states[7].last_voiced_at = time.monotonic() - 2
    await asyncio.sleep(0.2)
    assert floor.kinds() == ["opened", "discarded"]


@pytest.mark.asyncio
async def test_cleanup_discards_the_open_utterance(make_sink, vad):
    sink, floor = make_sink()
    feed(sink, vad, speech=10)
    sink.cleanup()
    await drain()
    assert floor.kinds() == ["opened", "discarded"]
    assert sink.user_states == {}


# --- Part 4: only your account is heard (L1), and where the speech is ---------------

@pytest.mark.asyncio
async def test_only_the_owners_audio_is_heard(make_sink, vad, capsys):
    sink, floor = make_sink(owners=lambda: {"8"})
    vad.upcoming.extend([0.9] * 10)
    for _ in range(20):  # USER is id 7
        sink.write(USER, MagicMock(pcm=FRAME))
    await drain()
    assert floor.kinds() == [] and sink.user_states == {}, "dropped before the VAD"
    assert vad.calls == 0
    assert capsys.readouterr().out.count("Not listening to Sam") == 1, "said once, not per packet"
    vad.upcoming.clear()

    owner = MagicMock(id=8, display_name="You", bot=False)
    feed(sink, vad, user=owner, speech=10)
    await drain()
    assert floor.kinds() == ["opened"]


@pytest.mark.asyncio
async def test_everyone_is_heard_when_no_owner_is_known_or_the_filter_fails(make_sink, vad):
    sink, floor = make_sink(owners=lambda: set())
    feed(sink, vad, speech=10)
    await drain()
    assert floor.kinds() == ["opened"]

    def broken():
        raise RuntimeError("no voiceprint store")

    sink, floor = make_sink(owners=broken)
    feed(sink, vad, speech=10)
    await drain()
    assert floor.kinds() == ["opened"]


def test_a_speaker_discord_cannot_name_is_heard():
    with patch("onnxruntime.InferenceSession"):
        sink = VADSink(RecordingFloor(), owners=lambda: {"8"})
    sink.write(None, MagicMock(pcm=b"\x00\x00\x00\x00", packet=MagicMock(ssrc=0)))
    assert list(sink.user_states) == ["default"]


@pytest.mark.asyncio
async def test_the_utterance_marks_where_the_speech_is(make_sink, vad):
    sink, floor = make_sink()
    feed(sink, vad, silence=8)               # pre-roll: background before you speak
    feed(sink, vad, speech=10, silence=3)    # the onset run opens it on its 5th window
    await drain()
    utterance = floor.utterances()[0]
    assert utterance.speech_start == 8 * WINDOW_BYTES, "past the pre-roll, at the onset run"
    assert utterance.speech_end == (8 + 10) * WINDOW_BYTES, "the last voiced window"
    assert len(utterance.pcm) == (8 + 10 + 3) * WINDOW_BYTES
    assert utterance.speech_ms() == 10 * WINDOW_MS
