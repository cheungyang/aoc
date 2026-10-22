#!/usr/bin/env python3
"""Times the voiceprint gate's speaker model on this machine (Part 4).

Run it on the NAS before the gate scores its first drive there:

    docker compose exec app python3 scripts/speaker_gate_bench.py

For each speaker model in the model caches, it prints how long one embedding
takes for 1, 1.5, 2 and 5 seconds of audio -- the gate's cost per step, and
for a long utterance -- and how long the voicing measure takes. The NAS CPU is
old: should a model's runtime use an instruction it lacks, this process dies,
not the bot.

    python3 scripts/speaker_gate_bench.py --runs 20
    python3 scripts/speaker_gate_bench.py --model sessions/models/wespeaker_en_voxceleb_resnet34.onnx
"""
import argparse
import glob
import os
import platform
import statistics
import sys
import time
from typing import List, Optional, Sequence, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import numpy as np  # noqa: E402

from core.voice import settings  # noqa: E402
from core.voice.listening import speaker_gate  # noqa: E402

DURATIONS_S = (1.0, 1.5, 2.0, 5.0)


def model_paths(explicit: Optional[Sequence[str]] = None) -> List[str]:
    """The models to time: `explicit`, else every .onnx file in the model caches."""
    if explicit:
        return list(explicit)
    caches = [os.path.join(os.environ.get("AOC_ASSET_ROOT", "/opt/aoc"), "models"),
              os.path.join(PROJECT_ROOT, "sessions", "models")]
    seen, paths = set(), []
    for cache in caches:
        for path in sorted(glob.glob(os.path.join(cache, "*.onnx"))):
            if os.path.basename(path) not in seen:
                seen.add(os.path.basename(path))
                paths.append(path)
    return paths


def speechlike(seconds: float, seed: int = 0) -> bytes:
    """Harmonics on a wandering pitch, plus noise, as 16 kHz 16-bit PCM. What
    the network costs doesn't depend on what's said."""
    rate = speaker_gate.SAMPLE_RATE
    t = np.arange(int(seconds * rate)) / rate
    phase = 2 * np.pi * np.cumsum(120 + 30 * np.sin(2 * np.pi * 0.7 * t)) / rate
    audio = 3000 * sum(np.sin(k * phase) / k for k in range(1, 10))
    audio = audio + np.random.default_rng(seed).normal(0, 300, len(t))
    return np.clip(audio, -32768, 32767).astype(np.int16).tobytes()


def _median_ms(fn, runs: int) -> Tuple[float, float]:
    fn()  # warm up
    times = []
    for _ in range(runs):
        started = time.perf_counter()
        fn()
        times.append((time.perf_counter() - started) * 1000)
    return statistics.median(times), max(times)


def time_model(model, runs: int) -> Tuple[float, List[Tuple[float, float, float]]]:
    """(load ms, [(seconds of audio, median ms, worst ms), ...]) for a SpeakerModel."""
    started = time.perf_counter()
    model._load()
    load_ms = (time.perf_counter() - started) * 1000
    rows = []
    for seconds in DURATIONS_S:
        pcm = speechlike(seconds)
        median, worst = _median_ms(lambda: model.embed(pcm), runs)
        rows.append((seconds, median, worst))
    return load_ms, rows


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Time the voiceprint gate's speaker model on this machine.")
    parser.add_argument("--runs", type=int, default=10, help="timed embeddings per audio length (default 10)")
    parser.add_argument("--model", action="append", help="a model file to time (repeatable); default: every cached one")
    args = parser.parse_args(argv)

    paths = model_paths(args.model)
    if not paths:
        print("No speaker model found: run `scripts/provision_assets.py --stage runtime --only speaker`.")
        return 1

    import onnxruntime
    print(f"Speaker models on {platform.node()} ({platform.machine()}), onnxruntime {onnxruntime.__version__}, "
          f"one thread, {args.runs} runs each:")
    for path in paths:
        name = os.path.basename(path)
        in_use = " (in use)" if name == settings.SPEAKER_MODEL_FILE else ""
        load_ms, rows = time_model(speaker_gate.SpeakerModel(path), args.runs)
        print(f"\n{name}{in_use}: loaded in {load_ms:.0f} ms")
        for seconds, median, worst in rows:
            print(f"  {seconds:>4.1f} s of audio: median {median:6.1f} ms, worst {worst:6.1f} ms")

    samples = np.frombuffer(speechlike(5.0), dtype=np.int16)
    median, _ = _median_ms(lambda: speaker_gate.voiced_share(samples), args.runs)
    print(f"\nVoicing measure, 5 s of audio: median {median:.1f} ms")
    print(f"The gate's first verdict comes on {settings.SPEAKER_FIRST_S:.1f} s of speech; a turn ends after "
          f"{settings.END_OF_TURN_COMPLETE_MS / 1000:.1f} to {settings.END_OF_TURN_CONTINUING_MS / 1000:.1f} s "
          f"of silence.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
