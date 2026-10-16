#!/usr/bin/env python3
"""Streaming STT benchmark: what the voice pipeline hears, and how fast.

Each case is spoken with a Chirp 3: HD voice (LINEAR16, 16 kHz) and played into
the production listening path in real time. The real VADSink (Silero) decides
onset, pauses and the adaptive end of turn, and the real SpeechRecognizer
streams each utterance to gemini-3.5-transcribe-live while it is heard. Frames
arrive every 20 ms like Discord's, and stop during silences like Discord's do,
so the watchdog ends turns exactly as it does in production.

Per utterance, from the VAD's view of the speech:

    endpoint  last voiced frame -> end of turn   (adaptive; was a fixed 900 ms)
    stt       end of turn -> transcript ready    (Phase 3 target: p50 <= 300 ms)
    ready     last voiced frame -> transcript    (what the listener waits for)

plus the live session's own timings, word error rate against the script, and
whether agent names came through. Unless --no-whisper is given, the same audio
also goes to local Whisper (greedy, the fallback) for comparison -- measured
on this machine, which is faster than the NAS.

Usage:
    python scripts/stt_benchmark.py                      # all cases, 2 voices, 2 runs
    python scripts/stt_benchmark.py --runs 1 --case names_daisy --voices Aoede

Needs GEMINI_API_KEY (transcription) and GOOGLE_TTS_API_KEY (synthesis).
Output: scratch/stt_benchmark/<timestamp>/ with summary.md, results.json and
the synthesized audio.
"""
import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
for path in (PROJECT_ROOT, SCRIPTS_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

import numpy as np  # noqa: E402

from core.voice import settings as voice_settings  # noqa: E402

DEFAULT_OUT_ROOT = os.path.join(PROJECT_ROOT, "scratch", "stt_benchmark")
STT_TARGET_P50_MS = 300

SAMPLE_RATE = 16_000
FRAME_MS = 20
FRAME_BYTES = SAMPLE_RATE * 2 * FRAME_MS // 1000  # 640 bytes of 16 kHz mono per Discord frame
# A Discord client sends a few silence frames when the speaker stops, then
# nothing at all until they speak again.
DISCORD_TAIL_FRAMES = 5
SETTLE_TIMEOUT_S = 8.0

DEFAULT_VOICES = ("Aoede", "Charon")


@dataclass(frozen=True)
class Case:
    id: str
    parts: Tuple[str, ...]
    gap_ms: int = 0              # silence between parts, with no packets
    terms: Tuple[str, ...] = ()  # names that must come through
    turns: int = 1               # turns the VAD should cut it into


CASES = (
    Case("names_daisy", ("Ask Daisy what's on my calendar tomorrow.",), terms=("Daisy",)),
    Case("names_frankie", ("Tell Frankie I did thirty push-ups this morning.",), terms=("Frankie",)),
    Case("names_sophie", ("Have Sophie draft a plan for the garage shelves.",), terms=("Sophie",)),
    Case("names_aki", ("Aki, design a new agent for my reading list.",), terms=("Aki",)),
    Case("command", ("Turn off the kitchen lights.",)),
    Case("timer", ("Set a timer for twenty five minutes.",)),
    Case("question", ("What's the weather going to be like this weekend?",)),
    Case("long", ("Remind me to call the dentist on Thursday at three thirty, "
                  "and to pick up the dry cleaning on the way home.",)),
    Case("stop", ("Stop.",)),
    # A pause after "the" of ~1.05 s in all (the gap, Discord's tail frames and
    # the trim margin): past the default wait, inside the continuation wait, so
    # only the adaptive rule keeps it one turn.
    Case("continuation", ("Remind me to call the", "dentist tomorrow morning."), gap_ms=900),
    # A finished sentence, then more: two turns (VoiceManager merges them if the
    # first wasn't answered yet; see the B19 tests).
    Case("split", ("Add milk to the shopping list.", "And eggs."), gap_ms=1000, turns=2),
)


def references(case: Case, turns: int) -> List[str]:
    """What each turn should say: its own part when the case splits into one
    turn per part, otherwise everything said."""
    if turns == len(case.parts) and turns > 1:
        return list(case.parts)
    return [" ".join(case.parts)] * max(turns, 1)


# --- audio ------------------------------------------------------------------------

def to_discord_frame(pcm16k: bytes) -> bytes:
    """16 kHz mono -> what voice_recv hands the sink: 48 kHz stereo. The sink's
    3-sample box filter recovers the original samples exactly."""
    samples = np.frombuffer(pcm16k, dtype=np.int16)
    tripled = np.repeat(samples, 3)
    return np.column_stack([tripled, tripled]).astype(np.int16).tobytes()


def frames(pcm16k: bytes) -> List[bytes]:
    out = []
    for offset in range(0, len(pcm16k), FRAME_BYTES):
        out.append(to_discord_frame(pcm16k[offset:offset + FRAME_BYTES].ljust(FRAME_BYTES, b"\0")))
    return out


SILENT_FRAME = to_discord_frame(bytes(FRAME_BYTES))


class FrameClock:
    """Paces writes at one frame per 20 ms of wall time, without drift."""

    def __init__(self):
        self.next = time.monotonic()

    async def tick(self) -> None:
        self.next += FRAME_MS / 1000
        delay = self.next - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)

    async def pause(self, ms: int) -> None:
        """A stretch with no packets at all."""
        await asyncio.sleep(ms / 1000)
        self.next = time.monotonic()


def synthesize(client, tts, text: str, voice: str) -> bytes:
    """16 kHz mono PCM of `text` in a Chirp 3: HD voice."""
    from tts_benchmark import wav_to_pcm

    response = client.synthesize_speech(
        input=tts.SynthesisInput(text=text),
        voice=tts.VoiceSelectionParams(language_code="en-US", name=voice),
        audio_config=tts.AudioConfig(audio_encoding=tts.AudioEncoding.LINEAR16, sample_rate_hertz=SAMPLE_RATE),
        timeout=30,
    )
    content = response.audio_content
    pcm, rate = wav_to_pcm(content) if content[:4] == b"RIFF" else (content, SAMPLE_RATE)
    if rate != SAMPLE_RATE:
        raise RuntimeError(f"Chirp returned {rate} Hz audio; asked for {SAMPLE_RATE}")
    return trim_silence(pcm)


def trim_silence(pcm: bytes, threshold: int = 300, margin_ms: int = 50) -> bytes:
    """Cuts a voice's leading and trailing silence down to `margin_ms`, so a
    case's gap is exactly the silence between its parts."""
    samples = np.frombuffer(pcm, dtype=np.int16)
    loud = np.flatnonzero(np.abs(samples.astype(np.int32)) > threshold)
    if not len(loud):
        return pcm
    margin = SAMPLE_RATE * margin_ms // 1000
    start, end = max(0, loud[0] - margin), min(len(samples), loud[-1] + margin)
    return samples[start:end].tobytes()


# --- the listening path -------------------------------------------------------------

@dataclass
class Heard:
    """One utterance as the pipeline handled it."""
    utterance: object
    vad_final_at: float
    stt_done_at: Optional[float] = None
    text: str = ""
    provider: str = ""


class BenchFloor:
    """Receives the VAD's utterance hooks and drives the recognizer the way
    the Floor does, timing each utterance instead of answering it."""

    def __init__(self, recognizer):
        self.stt = recognizer
        self.heard: List[Heard] = []
        self.open = set()
        self._tasks: List[asyncio.Task] = []

    def utterance_opened(self, utterance):
        self.open.add(utterance.id)
        self.stt.open(utterance)

    def utterance_audio(self, utterance, pcm):
        self.stt.audio(utterance, pcm)

    def utterance_paused(self, utterance):
        self.stt.pause(utterance)

    def utterance_resumed(self, utterance):
        self.stt.resume(utterance)

    def utterance_discarded(self, utterance):
        self.open.discard(utterance.id)
        self.stt.discard(utterance)

    def utterance_finished(self, utterance):
        self.open.discard(utterance.id)
        heard = Heard(utterance, vad_final_at=time.monotonic())
        self.heard.append(heard)
        self.stt.finish(utterance)
        self._tasks.append(asyncio.ensure_future(self._transcribe(heard)))

    async def _transcribe(self, heard: Heard):
        transcript = await self.stt.transcribe(heard.utterance)
        heard.stt_done_at = time.monotonic()
        heard.text, heard.provider = transcript.text, transcript.provider

    def settled(self) -> bool:
        return not self.open and all(task.done() for task in self._tasks)


async def listen(case_audio: Sequence[bytes], gap_ms: int, recognizer) -> List[Heard]:
    """Plays one case into a fresh VADSink in real time; returns what it heard."""
    from core.voice.vad_sink import VADSink

    floor = BenchFloor(recognizer)
    sink = VADSink(floor, loop=asyncio.get_running_loop())
    user = SimpleNamespace(id=1, display_name="Bench", bot=False)
    clock = FrameClock()
    try:
        for index, pcm in enumerate(case_audio):
            if index:
                await clock.pause(gap_ms)
            for frame in frames(pcm) + [SILENT_FRAME] * DISCORD_TAIL_FRAMES:
                await clock.tick()
                sink.write(user, SimpleNamespace(pcm=frame, packet=None))
        deadline = time.monotonic() + SETTLE_TIMEOUT_S
        while not floor.settled() and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
    finally:
        sink.cleanup()
    return floor.heard


# --- records and scoring ---------------------------------------------------------------

@dataclass
class TurnRecord:
    case: str
    voice: str
    run: int
    turn: int
    reference: str
    transcript: str
    provider: str
    audio_ms: float
    endpoint_ms: Optional[float]
    stt_ms: Optional[float]
    ready_ms: Optional[float]
    wer: float
    missed_terms: List[str]
    session: Dict[str, float] = field(default_factory=dict)
    whisper_ms: Optional[float] = None
    whisper_text: Optional[str] = None
    whisper_wer: Optional[float] = None
    whisper_missed_terms: Optional[List[str]] = None


@dataclass
class CaseRun:
    case: str
    voice: str
    run: int
    expected_turns: int
    turns: List[TurnRecord]

    @property
    def turns_ok(self) -> bool:
        return len(self.turns) == self.expected_turns


def _ms(start: Optional[float], end: Optional[float]) -> Optional[float]:
    if start is None or end is None:
        return None
    return round((end - start) * 1000, 1)


def score(case: Case, voice: str, run: int, heard: List[Heard]) -> CaseRun:
    from tts_benchmark import score_pronunciation

    refs = references(case, len(heard))
    records = []
    for index, item in enumerate(heard):
        utterance = item.utterance
        scored = score_pronunciation(refs[index], item.text, list(case.terms))
        timings = getattr(utterance.stream, "timings", None) or {}
        records.append(TurnRecord(
            case=case.id, voice=voice, run=run, turn=index + 1, reference=refs[index],
            transcript=item.text, provider=item.provider, audio_ms=round(utterance.duration_ms(), 1),
            endpoint_ms=_ms(utterance.speech_end_at, item.vad_final_at),
            stt_ms=_ms(item.vad_final_at, item.stt_done_at),
            ready_ms=_ms(utterance.speech_end_at, item.stt_done_at),
            wer=scored["wer"], missed_terms=scored["missed_terms"],
            session={k: round(v, 1) for k, v in timings.items()},
        ))
    return CaseRun(case.id, voice, run, case.turns, records)


async def add_whisper(records: List[TurnRecord], heard: List[Heard], case: Case, engine) -> None:
    """The old pipeline's STT stage: the whole utterance to Whisper after the turn ended."""
    from tts_benchmark import score_pronunciation

    for record, item in zip(records, heard):
        started = time.monotonic()
        text = await engine.transcribe(item.utterance.wav())
        record.whisper_ms = round((time.monotonic() - started) * 1000, 1)
        record.whisper_text = text
        scored = score_pronunciation(record.reference, text, list(case.terms))
        record.whisper_wer, record.whisper_missed_terms = scored["wer"], scored["missed_terms"]


# --- report ---------------------------------------------------------------------------

def _pct(values, q):
    from tts_benchmark import percentile

    return percentile(values, q)


def _fmt(value, unit=" ms"):
    return "–" if value is None else f"{value:.0f}{unit}"


def _fmt_rate(value):
    return "–" if value is None else f"{value:.1%}"


def summarize(runs: List[CaseRun]) -> Dict:
    turns = [t for r in runs for t in r.turns]
    live = [t for t in turns if t.provider == "gemini_live"]
    metric = lambda name, rows: [getattr(t, name) for t in rows]  # noqa: E731
    session = lambda key: [t.session.get(key) for t in live]  # noqa: E731
    named = [t for t in turns if t.missed_terms is not None]
    term_total = sum(len(next(c for c in CASES if c.id == t.case).terms) for t in named)
    whisper = [t for t in turns if t.whisper_ms is not None]
    stt_p50 = _pct(metric("stt_ms", live), 50)
    return {
        "utterances": len(turns),
        "fallbacks": len(turns) - len(live),
        "turns_ok": sum(r.turns_ok for r in runs),
        "case_runs": len(runs),
        "stt_p50": stt_p50, "stt_p90": _pct(metric("stt_ms", live), 90),
        "endpoint_p50": _pct(metric("endpoint_ms", turns), 50), "endpoint_p90": _pct(metric("endpoint_ms", turns), 90),
        "ready_p50": _pct(metric("ready_ms", live), 50), "ready_p90": _pct(metric("ready_ms", live), 90),
        "final_after_end_p50": _pct(session("final_after_end_ms"), 50),
        "connect_p50": _pct(session("connect_ms"), 50), "setup_p50": _pct(session("setup_ms"), 50),
        "wer_mean": (sum(t.wer for t in turns) / len(turns)) if turns else None,
        "terms_missed": sum(len(t.missed_terms) for t in named), "terms_total": term_total,
        "whisper_p50": _pct([t.whisper_ms for t in whisper], 50), "whisper_p90": _pct([t.whisper_ms for t in whisper], 90),
        "whisper_wer_mean": (sum(t.whisper_wer for t in whisper) / len(whisper)) if whisper else None,
        "whisper_terms_missed": sum(len(t.whisper_missed_terms or []) for t in whisper) if whisper else None,
        "target_met": stt_p50 is not None and stt_p50 <= STT_TARGET_P50_MS,
    }


def render(runs: List[CaseRun], meta: Dict) -> str:
    s = summarize(runs)
    verdict = "✅ met" if s["target_met"] else "❌ not met"
    lines = [
        "# Streaming STT benchmark", "",
        f"{meta['when']} · {meta['host']} · voices {', '.join(meta['voices'])} · {meta['runs']} run(s) · "
        f"mode {meta['mode']}", "",
        f"**STT stage p50 {_fmt(s['stt_p50'])} (target ≤ {STT_TARGET_P50_MS} ms: {verdict})**", "",
        "| Metric | p50 | p90 |", "|---|---|---|",
        f"| Endpoint: last voiced frame → end of turn (was a fixed 900 ms) | {_fmt(s['endpoint_p50'])} | {_fmt(s['endpoint_p90'])} |",
        f"| STT: end of turn → transcript | {_fmt(s['stt_p50'])} | {_fmt(s['stt_p90'])} |",
        f"| Ready: last voiced frame → transcript | {_fmt(s['ready_p50'])} | {_fmt(s['ready_p90'])} |",
        f"| Live session: transcript after the turn ended (its own clock) | {_fmt(s['final_after_end_p50'])} | |",
        f"| Live connect / setup (overlaps speech) | {_fmt(s['connect_p50'])} / {_fmt(s['setup_p50'])} | |",
    ]
    if s["whisper_p50"] is not None:
        lines.append(f"| Whisper {voice_settings.WHISPER_MODEL} greedy on this machine, after the turn ends | "
                     f"{_fmt(s['whisper_p50'])} | {_fmt(s['whisper_p90'])} |")
    lines += [
        "",
        f"- Utterances: {s['utterances']}; fell back to Whisper: {s['fallbacks']}.",
        f"- Turn segmentation as expected: {s['turns_ok']}/{s['case_runs']} case runs.",
        f"- Mean WER: live {_fmt_rate(s['wer_mean'])}"
        + (f", Whisper {_fmt_rate(s['whisper_wer_mean'])}" if s["whisper_wer_mean"] is not None else "") + ".",
        f"- Agent names missed: live {s['terms_missed']}/{s['terms_total']}"
        + (f", Whisper {s['whisper_terms_missed']}/{s['terms_total']}" if s["whisper_terms_missed"] is not None else "") + ".",
        "", "| Case | Voice | Run | Turns | Endpoint | STT | Ready | WER | Transcript |", "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in runs:
        turns_cell = f"{len(r.turns)}/{r.expected_turns}" + ("" if r.turns_ok else " ⚠️")
        if not r.turns:
            lines.append(f"| {r.case} | {r.voice} | {r.run} | {turns_cell} | | | | | (nothing heard) |")
        for t in r.turns:
            fallback = "" if t.provider == "gemini_live" else f" ({t.provider})"
            missed = f" — missed {', '.join(t.missed_terms)}" if t.missed_terms else ""
            lines.append(
                f"| {t.case} | {t.voice} | {t.run} | {turns_cell} | {_fmt(t.endpoint_ms)} | {_fmt(t.stt_ms)} | "
                f"{_fmt(t.ready_ms)} | {t.wer:.0%} | {t.transcript}{fallback}{missed} |"
            )
    return "\n".join(lines) + "\n"


# --- main -----------------------------------------------------------------------------

async def run(args) -> int:
    from core.util.config import Config
    from core.voice.engines.stt import SpeechRecognizer
    from tts_benchmark import API_KEY_ENV_VAR, Options, make_chirp_client

    config = Config()
    if not config.gemini_api_key:
        raise SystemExit("GEMINI_API_KEY is not set: there is nothing to stream to.")
    opts = Options(tts_api_key=(config.get(API_KEY_ENV_VAR) or "").strip())
    client, tts = make_chirp_client(opts)

    cases = [c for c in CASES if not args.case or c.id in args.case]
    voices = [f"en-US-Chirp3-HD-{name}" for name in args.voices]
    out_dir = os.path.abspath(args.out or os.path.join(DEFAULT_OUT_ROOT, datetime.now().strftime("%Y%m%d-%H%M%S")))
    os.makedirs(os.path.join(out_dir, "audio"), exist_ok=True)

    print(f"Synthesizing {len(cases)} cases x {len(voices)} voices...")
    audio: Dict[Tuple[str, str], List[bytes]] = {}
    for case in cases:
        for voice in voices:
            parts = [await asyncio.to_thread(synthesize, client, tts, text, voice) for text in case.parts]
            audio[(case.id, voice)] = parts
            from core.voice.utterance import pcm_to_wav
            with open(os.path.join(out_dir, "audio", f"{case.id}-{voice.rsplit('-', 1)[-1]}.wav"), "wb") as f:
                silence = bytes(SAMPLE_RATE * 2 * case.gap_ms // 1000)
                f.write(pcm_to_wav(silence.join(parts)))

    recognizer = SpeechRecognizer(provider="gemini_live", vocabulary=args.vocabulary)
    whisper = None
    if not args.no_whisper:
        from core.voice.engines.stt_engine import STTEngine
        print(f"Loading Whisper {voice_settings.WHISPER_MODEL} for the comparison...")
        whisper = await asyncio.to_thread(STTEngine, model_size=voice_settings.WHISPER_MODEL)

    runs: List[CaseRun] = []
    for run_index in range(1, args.runs + 1):
        for case in cases:
            for voice in voices:
                heard = await listen(audio[(case.id, voice)], case.gap_ms, recognizer)
                case_run = score(case, voice.rsplit("-", 1)[-1], run_index, heard)
                if whisper is not None:
                    await add_whisper(case_run.turns, heard, case, whisper)
                runs.append(case_run)
                cells = "; ".join(f"stt {_fmt(t.stt_ms)} endpoint {_fmt(t.endpoint_ms)} {t.transcript!r}" for t in case_run.turns)
                print(f"[run {run_index}] {case.id} / {case_run.voice}: {len(heard)} turn(s) {cells or '(nothing heard)'}")

    meta = {"when": datetime.now().strftime("%Y-%m-%d %H:%M"), "host": os.uname().nodename,
            "voices": list(args.voices), "runs": args.runs, "mode": voice_settings.STT_MODE}
    report = render(runs, meta)
    with open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8") as f:
        f.write(report)
    with open(os.path.join(out_dir, "results.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "summary": summarize(runs),
                   "runs": [{**asdict(r), "turns_ok": r.turns_ok} for r in runs]}, f, indent=2)
    print()
    print(report)
    print(f"results: {out_dir}")
    return 0 if summarize(runs)["target_met"] else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark streaming STT through the production listening path.")
    parser.add_argument("--runs", type=int, default=2, help="Runs per case and voice (default 2).")
    parser.add_argument("--case", action="append", choices=[c.id for c in CASES], help="Only this case (repeatable).")
    parser.add_argument("--voices", type=lambda s: [v.strip() for v in s.split(",") if v.strip()],
                        default=list(DEFAULT_VOICES), help="Chirp 3: HD voice names (default: Aoede,Charon).")
    parser.add_argument("--no-whisper", action="store_true", help="Skip the local Whisper comparison.")
    parser.add_argument("--out", help="Output directory (default scratch/stt_benchmark/<timestamp>).")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    args = build_parser().parse_args(argv)
    from core.loaders.agents_loader import AgentsLoader
    from core.voice.engines.stt import agent_vocabulary

    args.vocabulary = agent_vocabulary(AgentsLoader())  # what production biases toward
    return asyncio.run(run(args))


if __name__ == "__main__":
    from _bootstrap import ensure_project_interpreter

    ensure_project_interpreter(probe_module="websockets")
    sys.exit(main())
