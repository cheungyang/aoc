"""stt_benchmark: frame conversion, references, the benchmark floor, and the report."""
import asyncio
import os
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

import stt_benchmark as bench  # noqa: E402
from core.voice.utterance import Transcript, Utterance  # noqa: E402


def test_discord_frames_decimate_back_to_the_original_samples():
    """The sink averages L/R and 3 consecutive 48 kHz samples; the benchmark's
    upsampling must survive that exactly, or it would be testing different audio."""
    samples = (np.arange(320, dtype=np.int16) * 50 - 8000).astype(np.int16)
    stereo = np.frombuffer(bench.to_discord_frame(samples.tobytes()), dtype=np.int16).reshape(-1, 2)
    assert stereo.shape == (960, 2)  # 20 ms of 48 kHz stereo
    mono = (stereo[:, 0].astype(np.float32) + stereo[:, 1]) / 2
    recovered = (mono[0::3] + mono[1::3] + mono[2::3]) / 3
    assert np.array_equal(recovered.astype(np.int16), samples)


def test_frames_pad_the_last_one():
    out = bench.frames(bytes(bench.FRAME_BYTES * 2 + 10))
    assert len(out) == 3
    assert all(len(frame) == 3840 for frame in out)


def test_trim_silence_keeps_a_margin_around_the_speech():
    speech = np.full(1600, 5000, dtype=np.int16)  # 100 ms
    padded = np.concatenate([np.zeros(8000, np.int16), speech, np.zeros(8000, np.int16)]).tobytes()
    trimmed = np.frombuffer(bench.trim_silence(padded, margin_ms=50), dtype=np.int16)
    assert len(trimmed) == 1600 + 2 * 800 - 1
    assert bench.trim_silence(bytes(3200)) == bytes(3200)


def test_references_follow_how_the_case_was_cut():
    split = next(c for c in bench.CASES if c.id == "split")
    assert bench.references(split, 2) == list(split.parts)
    assert bench.references(split, 1) == [" ".join(split.parts)]
    continuation = next(c for c in bench.CASES if c.id == "continuation")
    assert bench.references(continuation, 1) == ["Remind me to call the dentist tomorrow morning."]


class FakeRecognizer:
    def __init__(self):
        self.calls = []

    def open(self, u):
        self.calls.append("open")

    def audio(self, u, pcm):
        self.calls.append("audio")

    def pause(self, u):
        self.calls.append("pause")

    def resume(self, u):
        self.calls.append("resume")

    def finish(self, u):
        self.calls.append("finish")

    def discard(self, u):
        self.calls.append("discard")

    async def transcribe(self, u):
        await asyncio.sleep(0.01)
        return Transcript("hello there", "gemini_live")


@pytest.mark.asyncio
async def test_the_bench_floor_drives_the_recognizer_and_times_it():
    recognizer = FakeRecognizer()
    floor = bench.BenchFloor(recognizer)
    kept, dropped = Utterance(), Utterance()
    floor.utterance_opened(kept)
    floor.utterance_audio(kept, b"\x00\x00")
    floor.utterance_paused(kept)
    floor.utterance_resumed(kept)
    floor.utterance_opened(dropped)
    floor.utterance_discarded(dropped)
    assert not floor.settled()
    floor.utterance_finished(kept)
    while not floor.settled():
        await asyncio.sleep(0.005)

    assert recognizer.calls == ["open", "audio", "pause", "resume", "open", "discard", "finish"]
    [heard] = floor.heard
    assert heard.text == "hello there" and heard.provider == "gemini_live"
    assert heard.stt_done_at - heard.vad_final_at >= 0.009


def _record(**overrides):
    values = dict(case="names_daisy", voice="Aoede", run=1, turn=1, reference="Ask Daisy.",
                  transcript="Ask Daisy.", provider="gemini_live", audio_ms=1500, endpoint_ms=520,
                  stt_ms=40, ready_ms=560, wer=0.0, missed_terms=[], session={"final_after_end_ms": 410})
    values.update(overrides)
    return bench.TurnRecord(**values)


def test_the_summary_judges_the_stt_target_on_live_transcripts_only():
    runs = [
        bench.CaseRun("names_daisy", "Aoede", 1, 1, [_record(stt_ms=40)]),
        bench.CaseRun("names_daisy", "Aoede", 2, 1, [_record(stt_ms=120, run=2)]),
        # A fallback is reported, but its Whisper time isn't the streaming stage.
        bench.CaseRun("names_daisy", "Aoede", 3, 1, [_record(stt_ms=2400, provider="whisper_fallback", run=3,
                                                             missed_terms=["Daisy"])]),
    ]
    summary = bench.summarize(runs)
    assert summary["fallbacks"] == 1
    assert summary["stt_p50"] <= bench.STT_TARGET_P50_MS
    assert summary["target_met"]
    assert (summary["terms_missed"], summary["terms_total"]) == (1, 3)

    report = bench.render(runs, {"when": "now", "host": "mac", "voices": ["Aoede"], "runs": 3, "mode": "SMART"})
    assert "target ≤ 300 ms: ✅ met" in report
    assert "(whisper_fallback)" in report and "missed Daisy" in report


def test_the_report_survives_a_run_that_heard_nothing():
    runs = [bench.CaseRun("stop", "Aoede", 1, 1, [])]
    summary = bench.summarize(runs)
    assert summary["stt_p50"] is None and not summary["target_met"]
    report = bench.render(runs, {"when": "now", "host": "mac", "voices": ["Aoede"], "runs": 1, "mode": "SMART"})
    assert "(nothing heard)" in report and "❌ not met" in report


@pytest.mark.asyncio
async def test_the_frame_clock_paces_twenty_ms_frames():
    clock = bench.FrameClock()
    loop = asyncio.get_running_loop()
    started = loop.time()
    for _ in range(5):
        await clock.tick()
    assert loop.time() - started >= 0.09
