"""speaker_gate_bench: which models it times, the audio it times them on, and the report."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

import speaker_gate_bench as bench  # noqa: E402
from core.voice.listening.utterance import BYTES_PER_MS  # noqa: E402
from tests.helpers import FakeSpeakerModel  # noqa: E402


def test_speechlike_audio_is_16khz_16bit_and_voiced():
    pcm = bench.speechlike(1.5)
    assert len(pcm) == 1500 * BYTES_PER_MS
    from core.voice.listening.speaker_gate import voiced_share
    assert voiced_share(np.frombuffer(pcm, dtype=np.int16)) > 0.9


def test_it_times_the_given_models_else_every_cached_one_once(monkeypatch, tmp_path):
    assert bench.model_paths(["/x/a.onnx"]) == ["/x/a.onnx"]
    assets, project = tmp_path / "assets" / "models", tmp_path / "project" / "sessions" / "models"
    for cache, names in ((assets, ["b.onnx"]), (project, ["a.onnx", "b.onnx", "notes.txt"])):
        cache.mkdir(parents=True)
        for name in names:
            (cache / name).write_bytes(b"")
    monkeypatch.setenv("AOC_ASSET_ROOT", str(tmp_path / "assets"))
    monkeypatch.setattr(bench, "PROJECT_ROOT", str(tmp_path / "project"))
    assert bench.model_paths() == [str(assets / "b.onnx"), str(project / "a.onnx")]


def test_time_model_reports_each_length():
    model = FakeSpeakerModel()
    load_ms, rows = bench.time_model(model, runs=2)
    assert model.loaded and load_ms >= 0
    assert [seconds for seconds, _, _ in rows] == list(bench.DURATIONS_S)
    assert model.embedded.count(1000) == 3, "one warm-up, then the timed runs"


def test_main_without_a_model_says_how_to_get_one(monkeypatch, capsys):
    monkeypatch.setattr(bench, "model_paths", lambda explicit=None: [])
    assert bench.main([]) == 1
    assert "--only speaker" in capsys.readouterr().out


def test_main_prints_a_line_per_length(monkeypatch, capsys):
    monkeypatch.setattr(bench, "model_paths", lambda explicit=None: ["/models/fake.onnx"])
    monkeypatch.setattr(bench.speaker_gate, "SpeakerModel", lambda path: FakeSpeakerModel())
    assert bench.main(["--runs", "1"]) == 0
    out = capsys.readouterr().out
    assert "fake.onnx: loaded in" in out
    timed = [line for line in out.splitlines() if line.startswith("  ") and "s of audio: median" in line]
    assert len(timed) == len(bench.DURATIONS_S)
    assert "Voicing measure, 5 s of audio" in out
