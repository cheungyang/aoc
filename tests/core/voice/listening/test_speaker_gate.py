"""The voiceprint gate: features, voicing, verdicts, the voiceprint, and shadow scoring.

The network is replaced by `FakeSpeakerModel` (tests/helpers.py), whose
embedding is chosen so that its similarity to the voiceprint [1, 0] is
whatever the test scripts for that much audio. One test runs the real model,
when it's installed.
"""
import asyncio
import json
import os
import stat
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

from core.storage.speaker_gate_log import Effect, GateLog, Mode
from core.voice import settings
from core.voice.listening import speaker_gate as sg
from core.voice.listening.utterance import BYTES_PER_MS, SAMPLE_RATE, Transcript, Utterance

from tests.helpers import FAKE_VOICEPRINT, FakeSpeakerModel as FakeModel

YOU = np.array(FAKE_VOICEPRINT)


def pcm(ms, value=1000):
    return np.full(ms * BYTES_PER_MS // 2, value, dtype=np.int16).tobytes()


def voiceprint(model=FakeModel, user_id="42"):
    return sg.Voiceprint(embedding=list(YOU), model=model.name, enrolled_at=time.time(), readings=4,
                         consistency=0.8, user_id=user_id)


def utterance(speech_ms=1800, preroll_ms=0, tail_ms=0, text="Move my dentist to Friday.", user_id=42, ducked=False):
    u = Utterance(user=MagicMock(id=user_id, display_name="Sam", bot=False))
    u.pcm += pcm(preroll_ms, 5) + pcm(speech_ms) + pcm(tail_ms, 5)
    u.speech_start = preroll_ms * BYTES_PER_MS
    u.speech_end = (preroll_ms + speech_ms) * BYTES_PER_MS
    u.transcript = Transcript(text, "gemini_live")
    u.ducked = ducked
    return u


def harmonic(seconds=1.0, pitch=140.0):
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (sum(np.sin(2 * np.pi * pitch * k * t) / k for k in range(1, 10)) * 4000).astype(np.int16)


# --- features ----------------------------------------------------------------

def test_mel_banks_are_80_triangles_over_the_power_spectrum():
    banks = sg.mel_banks()
    assert banks.shape == (80, 257)
    assert (banks >= 0).all() and (banks.max(axis=1) > 0).all()
    assert banks[:, -1].sum() == 0, "the Nyquist bin gets no weight, as in Kaldi"


def test_fbank_frames_25ms_every_10ms():
    assert sg.fbank(np.zeros(SAMPLE_RATE)).shape == (98, 80)  # 1 + (16000 - 400) // 160
    assert sg.fbank(np.zeros(399)).shape == (0, 80)
    assert np.isfinite(sg.fbank(harmonic(0.5))).all()


# --- voicing: a whisper has no pitch -------------------------------------------

def test_talking_is_voiced_and_noise_or_silence_is_not():
    assert sg.voiced_share(harmonic()) > 0.9
    noise = np.random.default_rng(0).normal(0, 3000, SAMPLE_RATE).astype(np.int16)
    assert sg.voiced_share(noise) < 0.1
    assert sg.voiced_share(np.zeros(SAMPLE_RATE, dtype=np.int16)) == 0.0
    assert sg.voiced_share(np.zeros(10, dtype=np.int16)) == 0.0


def test_voicing_does_not_depend_on_loudness():
    loud = harmonic()
    assert sg.voiced_share((loud // 20).astype(np.int16)) == pytest.approx(sg.voiced_share(loud), abs=0.05)


# --- verdicts --------------------------------------------------------------------

def test_judge_by_the_thresholds():
    assert sg.judge(settings.SPEAKER_ACCEPT) is sg.Verdict.YOU
    assert sg.judge(settings.SPEAKER_REJECT) is sg.Verdict.NOT_YOU
    assert sg.judge((settings.SPEAKER_ACCEPT + settings.SPEAKER_REJECT) / 2) is sg.Verdict.UNDECIDED
    assert sg.judge(None) is sg.Verdict.UNDECIDED


def test_borderline_is_anything_not_called_yours_or_yours_by_a_thin_margin():
    assert sg.borderline(sg.Verdict.NOT_YOU, 0.1)
    assert sg.borderline(sg.Verdict.UNDECIDED, None)
    assert sg.borderline(sg.Verdict.YOU, settings.SPEAKER_ACCEPT + settings.SPEAKER_BORDERLINE / 2)
    assert not sg.borderline(sg.Verdict.YOU, settings.SPEAKER_ACCEPT + settings.SPEAKER_BORDERLINE)


def test_assess_decides_on_the_first_second_when_it_can():
    model = FakeModel(lambda ms: 0.9)
    result = sg.assess(model, voiceprint(), pcm(3000))
    assert (result.verdict, result.decided_after_ms) == (sg.Verdict.YOU, 1000)
    assert result.scores == [(1000, 0.9)]
    assert result.score_full == 0.9 and model.embedded == [1000, 3000]


def test_assess_keeps_listening_every_half_second_until_decided():
    result = sg.assess(FakeModel(lambda ms: 0.5 if ms < 2000 else 0.2), voiceprint(), pcm(3000))
    assert result.verdict is sg.Verdict.NOT_YOU
    assert result.decided_after_ms == 2000
    assert [ms for ms, _ in result.scores] == [1000, 1500, 2000]


def test_assess_never_decided_keeps_the_last_score():
    result = sg.assess(FakeModel(lambda ms: 0.5), voiceprint(), pcm(1800))
    assert (result.verdict, result.decided_after_ms, result.score) == (sg.Verdict.UNDECIDED, None, 0.5)
    assert [ms for ms, _ in result.scores] == [1000, 1500, 1800]
    assert result.score_full == 0.5, "the last step was the whole utterance: not embedded again"


def test_assess_a_short_utterance_scores_it_whole_and_a_blip_not_at_all():
    assert sg.assess(FakeModel(), voiceprint(), pcm(800)).scores == [(800, 0.9)]
    blip = sg.assess(FakeModel(), voiceprint(), pcm(300))
    assert (blip.verdict, blip.score, blip.score_full) == (sg.Verdict.UNDECIDED, None, None)


def test_assessment_summary_is_compact_for_the_trace():
    result = sg.Assessment(sg.Verdict.YOU, 0.91234, 1000, voiced_share=0.876, compute_ms=7)
    assert result.summary() == {"verdict": "YOU", "score": 0.912, "decided_ms": 1000, "voiced": 0.88,
                                "compute_ms": 7}


# --- the voiceprint ----------------------------------------------------------------

def test_voiceprint_round_trips_privately(tmp_path):
    path = str(tmp_path / "voice" / "voiceprint.json")
    voiceprint().save(path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    loaded = sg.Voiceprint.load(path)
    assert loaded.embedding == list(YOU) and loaded.user_id == "42"
    assert loaded.similarity(YOU) == pytest.approx(1.0)


def test_a_missing_or_broken_voiceprint_is_none(tmp_path):
    assert sg.Voiceprint.load(str(tmp_path / "absent.json")) is None
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert sg.Voiceprint.load(str(broken)) is None


def test_the_voiceprint_lives_beside_memory_db_unless_told_otherwise(monkeypatch, tmp_path):
    from core.util.config import Config
    assert sg.voiceprint_path() == os.path.join(os.path.dirname(Config().memory_db_path),
                                                "voice", "voiceprint.json")
    monkeypatch.setenv("AOC_VOICE_DATA_DIR", str(tmp_path))
    assert sg.clips_dir() == str(tmp_path / "gate_audio")


def test_the_speaker_model_is_found_in_the_asset_cache_first(monkeypatch, tmp_path):
    assets, project = tmp_path / "assets", tmp_path / "project"
    for root in (assets / "models", project / "sessions" / "models"):
        root.mkdir(parents=True)
        (root / settings.SPEAKER_MODEL_FILE).write_bytes(b"onnx")
    monkeypatch.setenv("AOC_ASSET_ROOT", str(assets))
    monkeypatch.setattr(settings, "_PROJECT_ROOT", str(project))
    assert settings.speaker_model_path() == str(assets / "models" / settings.SPEAKER_MODEL_FILE)
    (assets / "models" / settings.SPEAKER_MODEL_FILE).unlink()
    assert settings.speaker_model_path() == str(project / "sessions" / "models" / settings.SPEAKER_MODEL_FILE)
    (project / "sessions" / "models" / settings.SPEAKER_MODEL_FILE).unlink()
    assert settings.speaker_model_path() is None


# --- the gate: configuration and owners ------------------------------------------------

def gate_with(model=None, saved=True, **config):
    gate = sg.SpeakerGate(transcribe=lambda u: _transcript(u), model=model or FakeModel())
    if saved:
        voiceprint().save(sg.voiceprint_path())
    elif os.path.exists(sg.voiceprint_path()):
        os.remove(sg.voiceprint_path())
    gate.configure({"speaker_gate": "shadow", **config})
    return gate


async def _transcript(u):
    return u.transcript


def test_configure_reads_the_mode_and_owners():
    gate = sg.SpeakerGate(model=FakeModel())
    assert gate.configure({"speaker_gate": "SHADOW", "owner_user_ids": [123, "456", " "]}) == "shadow"
    assert gate.owner_user_ids == {"123", "456"}
    assert gate.configure({"speaker_gate": "enforce"}) == "enforce", "not built yet: it scores as shadow does"
    assert gate.configure({"speaker_gate": "loud"}) == "off"
    assert gate.configure(None) == "off" and gate.owner_user_ids == set()


def test_configure_warms_the_model_only_when_it_will_score():
    model = FakeModel()
    gate = sg.SpeakerGate(model=model)
    gate.configure({"speaker_gate": "shadow"})  # no voiceprint yet
    gate._executor.submit(lambda: None).result()
    assert not model.loaded
    voiceprint().save(sg.voiceprint_path())
    gate.configure({"speaker_gate": "shadow"})  # a join reads it again
    gate._executor.submit(lambda: None).result()
    assert model.loaded and gate.scoring()


def test_owners_are_the_configured_accounts_else_the_one_you_enrolled_from():
    assert gate_with().owner_ids() == {"42"}
    assert gate_with(owner_user_ids=["7"]).owner_ids() == {"7"}
    assert gate_with(saved=False).owner_ids() == set(), "nobody known: everyone is heard"
    off = gate_with()
    off.configure({"speaker_gate": "off"})
    assert off.owner_ids() == set(), "off turns the owner filter off too"


def test_a_voiceprint_from_another_model_is_not_used_but_still_names_you():
    gate = gate_with()

    class Other(FakeModel):
        name = "other.onnx"

    gate._model = Other()
    assert gate.voiceprint is None and not gate.scoring()
    assert gate.saved_voiceprint() is not None and gate.owner_ids() == {"42"}


# --- shadow: score and record, change nothing -------------------------------------------

async def _observed(gate, u, effect=Effect.TURN):
    gate.observe(u, effect)
    await asyncio.gather(*list(gate._tasks))
    return GateLog().to_review(since=0, borderline_below=2.0)


@pytest.mark.asyncio
async def test_shadow_scores_the_speech_and_records_what_the_utterance_did():
    model = FakeModel(lambda ms: 0.9)
    gate = gate_with(model)
    u = utterance(speech_ms=1800, preroll_ms=700, tail_ms=900, ducked=True)
    (record,) = await _observed(gate, u, Effect.BARGE_IN)

    assert model.embedded == [1000, 1800], "scored on the speech: no pre-roll, no end-of-turn silence"
    assert u.gate.verdict is sg.Verdict.YOU
    assert (record.mode, record.verdict, record.effect, record.over_reply) == (Mode.SHADOW, "YOU", Effect.BARGE_IN, True)
    assert (record.duration_ms, record.decided_after_ms, record.score) == (1800, 1000, 0.9)
    assert (record.user_id, record.transcript, record.model) == ("42", "Move my dentist to Friday.", "fake.onnx")
    assert record.audio_path is None, "a confident match keeps no audio"


@pytest.mark.asyncio
async def test_a_would_be_rejection_keeps_its_audio_for_review():
    gate = gate_with(FakeModel(lambda ms: 0.2))
    u = utterance(preroll_ms=500)
    (record,) = await _observed(gate, u)
    assert record.verdict == "NOT_YOU"
    assert record.audio_path == os.path.join(sg.clips_dir(), f"{record.id}.wav")
    import wave
    with wave.open(record.audio_path) as clip:
        assert clip.getframerate() == SAMPLE_RATE
        assert clip.getnframes() * 2 == len(u.pcm), "the whole utterance, pre-roll and all"


@pytest.mark.asyncio
async def test_nothing_is_scored_when_off_without_a_voiceprint_or_while_enrolling():
    off = gate_with()
    off.configure({"speaker_gate": "off"})
    assert await _observed(off, utterance()) == []
    assert await _observed(gate_with(saved=False), utterance()) == []
    enrolling = gate_with()
    enrolling.enrollment = object()
    assert await _observed(enrolling, utterance()) == []


@pytest.mark.asyncio
async def test_a_scoring_error_is_logged_and_records_nothing(capsys):
    class Broken(FakeModel):
        def embed(self, pcm):
            raise RuntimeError("bad graph")

    assert await _observed(gate_with(Broken()), utterance()) == []
    assert "Could not score" in capsys.readouterr().out


def test_old_clips_are_dropped_when_a_new_one_is_kept():
    gate = gate_with()
    os.makedirs(sg.clips_dir())
    old = os.path.join(sg.clips_dir(), "1.wav")
    open(old, "wb").close()
    stale = time.time() - (settings.GATE_AUDIO_KEEP_DAYS + 1) * 86400
    os.utime(old, (stale, stale))
    gate._keep_clip(2, pcm(100))
    assert sorted(os.listdir(sg.clips_dir())) == ["2.wav"]


# --- enrollment and reset ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_enroll_averages_the_readings_speech_and_saves_it():
    model = FakeModel(lambda ms: 0.8 if ms < 2600 else 0.6)
    gate = sg.SpeakerGate(model=model)
    readings = [utterance(speech_ms=ms, preroll_ms=600, tail_ms=900) for ms in (2500, 3000, 2800)]
    saved = await gate.enroll(readings)

    assert model.embedded == [2500, 3000, 2800], "the speech only"
    assert (saved.readings, saved.user_id, saved.model) == (3, "42", "fake.onnx")
    assert saved.consistency == pytest.approx(round(float(np.dot([0.8, 0.6], [0.6, 0.8])), 3))
    assert np.linalg.norm(saved.embedding) == pytest.approx(1.0, abs=1e-5)
    assert gate.voiceprint == saved, "in use at once"
    assert json.load(open(sg.voiceprint_path()))["readings"] == 3


@pytest.mark.asyncio
async def test_enroll_needs_two_usable_readings_and_the_model():
    with pytest.raises(ValueError):
        await sg.SpeakerGate(model=FakeModel()).enroll([utterance(), utterance(speech_ms=300)])
    gate = sg.SpeakerGate()
    gate._model = None
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "speaker_model_path", lambda: None)
        with pytest.raises(RuntimeError):
            await gate.enroll([utterance(), utterance()])


def test_reset_deletes_the_voiceprint_and_the_clips():
    gate = gate_with()
    gate._keep_clip(1, pcm(100))
    assert gate.reset() is True
    assert not os.path.exists(sg.voiceprint_path()) and not os.path.exists(sg.clips_dir())
    assert gate.voiceprint is None and gate.owner_ids() == set()
    assert gate.reset() is False


# --- the real model, when it's installed ----------------------------------------------------

@pytest.mark.skipif(settings.speaker_model_path() is None, reason="speaker model not installed")
def test_the_real_model_embeds_to_a_unit_vector():
    model = sg.SpeakerModel(settings.speaker_model_path())
    audio = harmonic(1.5).tobytes()
    first, second = model.embed(audio), model.embed(audio)
    assert first.shape == (512,) and np.linalg.norm(first) == pytest.approx(1.0)
    assert float(first @ second) == pytest.approx(1.0)
    assert model.embed(audio[: 400 * BYTES_PER_MS]) is None, "too short to embed"
