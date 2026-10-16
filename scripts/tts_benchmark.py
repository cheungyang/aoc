#!/usr/bin/env python3
"""TTS benchmark and voice audition (voice-first plan, Phase 0, section 4.3).

Measures today's engine against the candidates that could replace it, on the
kind of replies this system actually speaks (scripts/tts_benchmark_utterances.json):

    edge          edge-tts, today's engine (core/voice/tts_engine.py)
    chirp-unary   Chirp 3: HD on Cloud Text-to-Speech, one SynthesizeSpeech call per sentence
    chirp-stream  Chirp 3: HD, one bidirectional StreamingSynthesize call fed clause by clause
    gemini        Gemini 3.8 Flash-Lite TTS over the Interactions API, audio streamed back

Usage (from the repo root):

    python scripts/tts_benchmark.py                        # 25 utterances x 3 runs, every metric
    python scripts/tts_benchmark.py --limit 8 --runs 2     # a quick subset, still one of each kind
    python scripts/tts_benchmark.py --candidates edge,gemini --reliability 0
    python scripts/tts_benchmark.py --ab-pairs             # also write blind A/B pairs vs edge-tts
    python scripts/tts_benchmark.py --ab-from RUN_DIR      # pairs from an earlier run, no synthesis
    python scripts/tts_benchmark.py --score-pairs RUN_DIR/pairs
    python scripts/tts_benchmark.py --audition             # one line per agent, in its own voice

Output goes to scratch/tts_benchmark/<timestamp>/ (gitignored and dockerignored):
results.json, summary.md, audio/<candidate>/<utterance>.{wav,mp3}, and pairs/ plus
ab_key/ when pairs are requested.

What each metric means here (targets are the plan's, verbatim):

- Time to first audio (TTFA): from the moment the first text is available to the
  first audio bytes, p50/p90 over utterances x runs; <= 400 ms p50 for streaming.
  Engines without bidirectional streaming are fed one sentence at a time, the way
  VoiceStream feeds edge-tts today, so on a clause-chunked reply their TTFA
  includes waiting for the first sentence to finish. Clause chunks arrive
  --chunk-interval-ms apart, standing in for the model's token stream. Each
  candidate's first call warms its connection and is reported separately as the
  cold start.
- Stalls: playback is simulated from chunk arrival times. It starts when the first
  chunk lands (plus --prebuffer-ms), and every chunk that arrives after the audio
  before it has finished playing is an underrun. Target: zero underruns, and
  synthesis time / audio duration (RTF) under 0.5 -- judged on the p90.
- Pronunciation (automated proxy): the synthesized audio is transcribed by
  gemini-3.5-transcribe (verbatim mode) and compared with the input. The
  transcriber writes "35 lb" and "7:45 p.m." even in verbatim mode, so both sides
  are normalized to spoken words before scoring. Reported as word error rate and
  as term misses: listed names and terms absent from the transcript, scaled to a
  25-utterance set (target <= 1). A miss can also be a transcription spelling --
  check the transcripts in results.json, and add real alternates to the data file.
- Naturalness: --ab-pairs writes randomized blind pairs against edge-tts and a
  separate answer key; you listen, fill in picks.csv, and --score-pairs counts how
  often each candidate was preferred (target >= 80%; a tie counts as not preferred).
- Reliability: --reliability N short requests per candidate (default 100); target
  zero errors and timeouts. Requests make one attempt each: no client retries.
- Cost: price x the expected ~840k spoken characters a month. Chirp 3: HD's first
  1M characters a month are free; Gemini TTS is billed per text and audio token,
  so its estimate uses the audio seconds and tokens per character measured here.

Credentials: Chirp reads a service-account key from the file named by
GOOGLE_TTS_CREDENTIALS_FILE (see .env.example); without it both Chirp candidates
are skipped with a message, not failed. Gemini uses GEMINI_API_KEY from .env.

Sources (checked 2026-10-15):
- Chirp 3: HD voices, speaking_rate 0.25-2.0, [pause] markup, custom pronunciations:
  https://cloud.google.com/text-to-speech/docs/chirp3-hd
- Bidirectional streaming, Chirp 3: HD only, one config request then text requests:
  https://cloud.google.com/text-to-speech/docs/create-audio-text-streaming
- "Streaming supports PCM, ALAW, MULAW and OGG_OPUS": StreamingAudioConfig in
  google-cloud-texttospeech 2.38.0 (google/cloud/texttospeech_v1/types/cloud_tts.py)
- Chirp 3: HD price, 0-1M characters a month free, then $30 per 1M characters:
  https://cloud.google.com/text-to-speech/pricing
- Gemini TTS: model ids gemini-3.8-flash-tts and gemini-3.8-flash-lite-tts, the
  Interactions API request shape, stream=true returning raw 24 kHz 16-bit mono PCM,
  the same 30 prebuilt voices as Chirp 3: HD:
  https://ai.google.dev/gemini-api/docs/speech-generation
- gemini-3.8-flash-lite-tts limits (8,192 input tokens):
  https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash-lite-tts
- Gemini prices: Flash-Lite TTS $0.50 per 1M text tokens in and $6 per 1M audio
  tokens out through 2026-12-31, $1 and $12 from 2027-01-01, 25 audio tokens per
  second; gemini-3.5-transcribe ~$0.005 per minute:
  https://ai.google.dev/gemini-api/docs/pricing
- Interactions API: SSE events (step.delta, interaction.completed with usage) and
  store=false: https://ai.google.dev/api/interactions-api
- Transcription: gemini-3.5-transcribe, verbatim mode, language_codes, inline audio:
  https://ai.google.dev/gemini-api/docs/transcribe

Running on the NAS (Synology DS220+, Celeron J4025, no AVX): everything here is
already in the image (numpy, soundfile, edge-tts, and httpx, which comes with
google-genai) except google-cloud-texttospeech, whose grpcio wheel is the one new
native dependency. Smoke-test it on the NAS first:
    python -c "import grpc; from google.cloud import texttospeech"
A wheel built for a newer CPU dies with SIGILL rather than an ImportError, and the
client imports grpc even for its REST transport, so on failure both Chirp
candidates are out on that host. Gemini is called over REST rather than through
the SDK because the image's google-genai predates the Interactions API it needs.
"""
import argparse
import asyncio
import base64
import csv
import io
import json
import math
import os
import platform
import random
import re
import sys
import time
import unicodedata
import wave
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.voice.settings import CHIRP3_HD_VOICES, DEFAULT_VOICE, chirp3_voice  # noqa: E402

UTTERANCES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tts_benchmark_utterances.json")
AGENTS_DIR = os.path.join(PROJECT_ROOT, "agents")
# Ignored by both .gitignore and .dockerignore, and visible on the NAS host
# through the `.:/app` bind mount.
DEFAULT_OUT_ROOT = os.path.join(PROJECT_ROOT, "scratch", "tts_benchmark")

CANDIDATES = ("edge", "chirp-unary", "chirp-stream", "gemini")
BASELINE = "edge"

CREDENTIALS_ENV_VAR = "GOOGLE_TTS_CREDENTIALS_FILE"
CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

from core.util.models import TRANSCRIBE as TRANSCRIBE_MODEL  # noqa: E402
from core.util.models import TTS_FLASH_LITE as GEMINI_TTS_MODEL  # noqa: E402
INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"

# main's voice_config.tts_voice today.
EDGE_VOICE = "en-US-AriaNeural"
# edge-tts always asks for audio-24khz-48kbitrate-mono-mp3, a constant bitrate,
# so a chunk's duration follows from its size.
EDGE_MP3_BITS_PER_SECOND = 48_000
# Chirp 3: HD and Gemini TTS both produce 24 kHz audio by default.
PCM_SAMPLE_RATE = 24_000
PCM_BYTES_PER_SECOND = PCM_SAMPLE_RATE * 2

# Targets, from the plan's section 4.3 table.
TARGET_TTFA_P50_MS = 400.0
TARGET_RTF = 0.5
TARGET_PREFERENCE = 0.80
TARGET_TERM_MISSES_PER_25 = 1.0

MONTHLY_SPOKEN_CHARS = 840_000
CHIRP_FREE_CHARS_PER_MONTH = 1_000_000
CHIRP_USD_PER_MILLION_CHARS = 30.0
AUDIO_TOKENS_PER_SECOND = 25
# (label, USD per 1M text input tokens, USD per 1M audio output tokens)
GEMINI_TTS_PRICES = (
    ("through 2026-12-31", 0.50, 6.00),
    ("from 2027-01-01", 1.00, 12.00),
)
# gemini-3.5-transcribe, USD per 1M tokens: audio in, text out.
TRANSCRIBE_PRICES = (2.00, 12.00)
# Used only when no Gemini synthesis succeeded, so there is nothing to measure.
NOMINAL_CHARS_PER_AUDIO_SECOND = 15.0
NOMINAL_CHARS_PER_TEXT_TOKEN = 4.0

# Workers have no audience, and script-executor has no model behind it.
AUDITION_EXCLUDED_AGENTS = {"graph-worker", "graph-worker-low", "script-executor"}

WARMUP_TEXT = "Okay."
# A gap shorter than this between chunks is measurement noise, not a stall.
UNDERRUN_TOLERANCE_S = 0.010
DEFAULT_PAIRS_SEED = 20261015


# ---------------------------------------------------------------------------
# Text normalization, word error rate, term matching
# ---------------------------------------------------------------------------

_ONES = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
)
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
_SCALES = ((10 ** 12, "trillion"), (10 ** 9, "billion"), (10 ** 6, "million"), (10 ** 3, "thousand"))
_ORDINAL_EXCEPTIONS = {
    "one": "first", "two": "second", "three": "third", "five": "fifth",
    "eight": "eighth", "nine": "ninth", "twelve": "twelfth",
}
_UNIT_WORDS = {
    "lb": "pounds", "lbs": "pounds", "kg": "kilograms", "oz": "ounces",
    "km": "kilometers", "mph": "miles per hour", "ft": "feet",
}
_SINGULAR_UNITS = {"pounds": "pound", "kilograms": "kilogram", "ounces": "ounce", "kilometers": "kilometer", "feet": "foot"}
_SCALE_WORDS = {"k": "thousand", "m": "million", "b": "billion", "thousand": "thousand", "million": "million", "billion": "billion"}
# Dropped from both sides before scoring. "and" because "one hundred and five"
# and "$1.50" said with or without it are the same speech; the rest are fillers
# a verbatim transcript keeps and the input never has.
_DROPPED_TOKENS = {"and", "um", "uh", "uhm", "erm", "er", "hmm", "mm", "oclock"}

_NUM = r"\d[\d,]*(?:\.\d+)?"
_CURRENCY_RE = re.compile(r"\$\s?(\d[\d,]*)(?:\.(\d+))?(?:\s?(thousand|million|billion|k|m|b)(?![a-z]))?", re.I)
# "1,234.56 dollars": the same amount written the other way round. The empty
# group stands in for the scale word so both patterns share `_currency`.
_DOLLARS_RE = re.compile(r"(\d[\d,]*)\.(\d{2})()\s?dollars\b", re.I)
_PERCENT_RE = re.compile(rf"({_NUM})\s?%")
_DEGREE_RE = re.compile(rf"({_NUM})\s?°\s?(?:([cf])(?![a-z]))?", re.I)
_TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_AMPM_RE = re.compile(r"\b([ap])\.?\s?m\b\.?", re.I)
_ORDINAL_RE = re.compile(r"\b(\d+)(?:st|nd|rd|th)\b", re.I)
_UNIT_RE = re.compile(rf"({_NUM})\s?(lbs?|kg|oz|km|mph|ft)\b", re.I)
_YEAR_RE = re.compile(r"(?<![\d,.$])\b(19\d{2}|20\d{2})\b(?![,.]\d)")
_NUMBER_RE = re.compile(_NUM)


def number_words(n: int) -> str:
    """American cardinal words, without "and": 1234 -> 'one thousand two hundred thirty four'."""
    if n < 0:
        return "minus " + number_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] + (" " + _ONES[ones] if ones else "")
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        return _ONES[hundreds] + " hundred" + (" " + number_words(rest) if rest else "")
    for value, name in _SCALES:
        if n >= value:
            head, rest = divmod(n, value)
            return number_words(head) + " " + name + (" " + number_words(rest) if rest else "")
    raise AssertionError("unreachable")


def ordinal_words(n: int) -> str:
    words = number_words(n).split()
    last = words[-1]
    if last in _ORDINAL_EXCEPTIONS:
        last = _ORDINAL_EXCEPTIONS[last]
    elif last.endswith("y"):
        last = last[:-1] + "ieth"
    else:
        last += "th"
    return " ".join(words[:-1] + [last])


def year_words(n: int) -> str:
    """2027 -> 'twenty twenty seven', 1905 -> 'nineteen oh five', 2005 -> 'two thousand five'."""
    if 2000 <= n <= 2009:
        return number_words(n)
    head, rest = divmod(n, 100)
    if rest == 0:
        return number_words(head) + " hundred"
    if rest < 10:
        return f"{number_words(head)} oh {number_words(rest)}"
    return f"{number_words(head)} {number_words(rest)}"


def decimal_words(raw: str) -> str:
    """'60,000' -> 'sixty thousand'; '21.5' -> 'twenty one point five'."""
    whole, _, frac = raw.replace(",", "").partition(".")
    words = number_words(int(whole or "0"))
    if frac:
        words += " point " + " ".join(_ONES[int(d)] for d in frac)
    return words


def _currency(match: "re.Match") -> str:
    whole, frac, scale = match.group(1), match.group(2), match.group(3)
    if scale:
        amount = decimal_words(whole + ("." + frac if frac else ""))
        return f" {amount} {_SCALE_WORDS[scale.lower()]} dollars "
    dollars = int(whole.replace(",", "") or "0")
    spoken = f" {number_words(dollars)} {'dollar' if dollars == 1 else 'dollars'}"
    if frac:
        cents = int((frac + "0")[:2])
        if cents:
            spoken += f" and {number_words(cents)} {'cent' if cents == 1 else 'cents'}"
    return spoken + " "


def _time(match: "re.Match") -> str:
    hour, minute = int(match.group(1)), int(match.group(2))
    if minute == 0:
        return f" {number_words(hour)} "
    if minute < 10:
        return f" {number_words(hour)} oh {number_words(minute)} "
    return f" {number_words(hour)} {number_words(minute)} "


def _unit(match: "re.Match") -> str:
    unit = _UNIT_WORDS[match.group(2).lower()]
    if match.group(1) == "1":
        unit = _SINGULAR_UNITS.get(unit, unit)
    return f" {decimal_words(match.group(1))} {unit} "


def _degree(match: "re.Match") -> str:
    scale = {"c": " celsius", "f": " fahrenheit"}.get((match.group(2) or "").lower(), "")
    return f" {decimal_words(match.group(1))} degrees{scale} "


def _merge_spelled_letters(tokens: List[str]) -> List[str]:
    """'c k a' -> 'cka', so a spelled-out acronym matches its written form."""
    merged: List[str] = []
    run: List[str] = []
    for token in tokens + [""]:
        if len(token) == 1 and token.isalpha():
            run.append(token)
            continue
        if len(run) >= 2:
            merged.append("".join(run))
        else:
            merged.extend(run)
        run = []
        if token:
            merged.append(token)
    return merged


def normalize_words(text: str) -> List[str]:
    """Reduces written or transcribed text to comparable spoken-word tokens.

    The input says "$1,234.56" and the transcriber may return "1,234.56 dollars"
    or the words; both become 'one thousand two hundred thirty four dollars fifty
    six cents'. Numbers, money, percentages, degrees, clock times, ordinals, years
    and a few units are expanded; accents, case and punctuation are dropped.
    """
    if not text:
        return []
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    text = _CURRENCY_RE.sub(_currency, text)
    text = _DOLLARS_RE.sub(_currency, text)
    text = _PERCENT_RE.sub(lambda m: f" {decimal_words(m.group(1))} percent ", text)
    text = _DEGREE_RE.sub(_degree, text)
    text = _TIME_RE.sub(_time, text)
    text = _AMPM_RE.sub(lambda m: f" {m.group(1).lower()}m ", text)
    text = _ORDINAL_RE.sub(lambda m: f" {ordinal_words(int(m.group(1)))} ", text)
    text = _UNIT_RE.sub(_unit, text)
    text = _YEAR_RE.sub(lambda m: f" {year_words(int(m.group(1)))} ", text)
    text = _NUMBER_RE.sub(lambda m: f" {decimal_words(m.group(0))} ", text)
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[-\u2013\u2014/]", " ", text)
    text = text.replace("'", "")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = [t for t in text.split() if t not in _DROPPED_TOKENS]
    return _merge_spelled_letters(tokens)


@dataclass
class WordErrors:
    ref_words: int
    substitutions: int
    deletions: int
    insertions: int

    @property
    def errors(self) -> int:
        return self.substitutions + self.deletions + self.insertions

    @property
    def wer(self) -> float:
        if self.ref_words == 0:
            return 0.0 if self.insertions == 0 else 1.0
        return self.errors / self.ref_words


def word_errors(ref: Sequence[str], hyp: Sequence[str]) -> WordErrors:
    """Levenshtein alignment over words, keeping the S/D/I breakdown."""
    # Each cell: (cost, substitutions, deletions, insertions).
    prev = [(j, 0, 0, j) for j in range(len(hyp) + 1)]
    for i in range(1, len(ref) + 1):
        cur = [(i, 0, i, 0)]
        for j in range(1, len(hyp) + 1):
            diag = prev[j - 1]
            if ref[i - 1] == hyp[j - 1]:
                best = diag
            else:
                best = (diag[0] + 1, diag[1] + 1, diag[2], diag[3])
            up = prev[j]
            if up[0] + 1 < best[0]:
                best = (up[0] + 1, up[1], up[2] + 1, up[3])
            left = cur[j - 1]
            if left[0] + 1 < best[0]:
                best = (left[0] + 1, left[1], left[2], left[3] + 1)
            cur.append(best)
        prev = cur
    _, subs, dels, ins = prev[-1]
    return WordErrors(len(ref), subs, dels, ins)


def term_found(variants: Sequence[str], hyp_tokens: Sequence[str]) -> bool:
    """Whether any spelling of a term appears in the transcript, ignoring spacing.

    Windows of consecutive transcript words are joined without spaces, so
    "xiao long bao" counts for "xiaolongbao" and "Fitbod" for "Fit bod".
    """
    for variant in variants:
        target_tokens = normalize_words(variant)
        target = "".join(target_tokens)
        if not target:
            continue
        span = len(target_tokens) + 3
        for i in range(len(hyp_tokens)):
            joined = ""
            for j in range(i, min(len(hyp_tokens), i + span)):
                joined += hyp_tokens[j]
                if joined == target:
                    return True
                if len(joined) >= len(target):
                    break
    return False


def score_pronunciation(reference: str, transcript: str, terms: Sequence[str],
                        alternates: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
    ref = normalize_words(reference)
    hyp = normalize_words(transcript)
    errs = word_errors(ref, hyp)
    alternates = alternates or {}
    missed = [t for t in terms if not term_found([t] + list(alternates.get(t, [])), hyp)]
    return {
        "ref_words": errs.ref_words,
        "substitutions": errs.substitutions,
        "deletions": errs.deletions,
        "insertions": errs.insertions,
        "wer": round(errs.wer, 4),
        "terms": len(terms),
        "missed_terms": missed,
    }


# ---------------------------------------------------------------------------
# Statistics and playback simulation
# ---------------------------------------------------------------------------

def percentile(values: Iterable[Optional[float]], q: float) -> Optional[float]:
    """Linear-interpolated percentile (numpy's default method); None when empty."""
    data = sorted(v for v in values if v is not None)
    if not data:
        return None
    if len(data) == 1:
        return float(data[0])
    position = (len(data) - 1) * q / 100.0
    low = int(math.floor(position))
    high = min(low + 1, len(data) - 1)
    return float(data[low] + (data[high] - data[low]) * (position - low))


@dataclass
class AudioChunk:
    arrival_s: float  # since the first text was available
    duration_s: float


@dataclass
class Playback:
    underruns: int
    stall_s: float
    finish_s: float


def simulate_playback(chunks: Sequence[AudioChunk], prebuffer_s: float = 0.0,
                      tolerance_s: float = UNDERRUN_TOLERANCE_S) -> Playback:
    """Plays chunks in arrival order and counts the times the speaker runs dry.

    Playback starts `prebuffer_s` after the first chunk lands. `playhead` is when
    the audio queued so far finishes; a chunk landing later than that is an
    underrun, and playback resumes as it arrives.
    """
    if not chunks:
        return Playback(0, 0.0, 0.0)
    playhead = chunks[0].arrival_s + prebuffer_s
    underruns, stall = 0, 0.0
    for chunk in chunks:
        gap = chunk.arrival_s - playhead
        if gap > tolerance_s:
            underruns += 1
            stall += gap
        if gap > 0:
            playhead = chunk.arrival_s
        playhead += chunk.duration_s
    return Playback(underruns, stall, playhead)


_SENTENCE_END_RE = re.compile(r"[.!?\u2026][\"')\]]*\s*$")


def group_into_sentences(pieces: Sequence[str], schedule: Sequence[float]) -> List[Tuple[str, float]]:
    """Joins clause chunks into sentences, each available when its last clause is.

    This is how an engine without bidirectional streaming is fed: VoiceStream
    holds tokens until a sentence completes, then sends that sentence.
    """
    groups: List[Tuple[str, float]] = []
    buffer = ""
    for piece, at in zip(pieces, schedule):
        buffer += piece
        if _SENTENCE_END_RE.search(buffer):
            groups.append((buffer.strip(), at))
            buffer = ""
    if buffer.strip():
        groups.append((buffer.strip(), schedule[-1] if schedule else 0.0))
    return groups


def utterance_pieces(utterance: Dict[str, Any], chunk_interval_s: float) -> Tuple[List[str], List[float]]:
    chunks = utterance.get("chunks")
    if chunks:
        return list(chunks), [i * chunk_interval_s for i in range(len(chunks))]
    return [utterance["text"]], [0.0]


def stratified_selection(utterances: Sequence[Dict[str, Any]], limit: Optional[int]) -> List[Dict[str, Any]]:
    """The first `limit` utterances taken round-robin across categories, in file order.

    `--limit 8` then still covers acknowledgements, numbers, names, the chunked
    reply and the long one, instead of eight acknowledgements.
    """
    if not limit or limit >= len(utterances):
        return list(utterances)
    queues: Dict[str, List[Dict[str, Any]]] = {}
    for utterance in utterances:
        queues.setdefault(utterance["category"], []).append(utterance)
    picked: List[Dict[str, Any]] = []
    while len(picked) < limit:
        for queue in queues.values():
            if queue and len(picked) < limit:
                picked.append(queue.pop(0))
    order = {u["id"]: i for i, u in enumerate(utterances)}
    return sorted(picked, key=lambda u: order[u["id"]])


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------

def chirp_monthly_cost(chars: float, free_chars: float = CHIRP_FREE_CHARS_PER_MONTH,
                       usd_per_million: float = CHIRP_USD_PER_MILLION_CHARS) -> float:
    return max(0.0, chars - free_chars) * usd_per_million / 1e6


def gemini_monthly_cost(chars: float, audio_seconds_per_char: float, text_tokens_per_char: float,
                        usd_per_million_in: float, usd_per_million_out: float) -> float:
    audio_tokens = chars * audio_seconds_per_char * AUDIO_TOKENS_PER_SECOND
    text_tokens = chars * text_tokens_per_char
    return text_tokens * usd_per_million_in / 1e6 + audio_tokens * usd_per_million_out / 1e6


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

def pcm_to_wav(pcm: bytes, rate: int = PCM_SAMPLE_RATE) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buffer.getvalue()


def wav_to_pcm(data: bytes) -> Tuple[bytes, int]:
    """16-bit mono PCM and its sample rate from WAV bytes (mixes stereo down)."""
    with wave.open(io.BytesIO(data), "rb") as src:
        rate, channels, width = src.getframerate(), src.getnchannels(), src.getsampwidth()
        frames = src.readframes(src.getnframes())
    if width != 2:
        raise ValueError(f"expected 16-bit WAV, got {8 * width}-bit")
    if channels > 1:
        import numpy as np
        samples = np.frombuffer(frames, dtype="<i2").reshape(-1, channels).mean(axis=1)
        frames = samples.astype("<i2").tobytes()
    return frames, rate


def strip_riff(data: bytes) -> bytes:
    """Raw PCM from bytes that may start with a WAV header."""
    if data[:4] != b"RIFF":
        return data
    try:
        return wav_to_pcm(data)[0]
    except (wave.Error, EOFError, ValueError):
        return data[44:]


def decode_audio(data: bytes, audio_format: str, rate: int = PCM_SAMPLE_RATE):
    """(int16 numpy samples, sample rate) from 'pcm', 'wav' or 'mp3' bytes."""
    import numpy as np
    if audio_format == "pcm":
        return np.frombuffer(data, dtype="<i2").copy(), rate
    if audio_format == "wav":
        pcm, wav_rate = wav_to_pcm(data)
        return np.frombuffer(pcm, dtype="<i2").copy(), wav_rate
    import soundfile as sf
    samples, mp3_rate = sf.read(io.BytesIO(data), dtype="int16", always_2d=False)
    if samples.ndim > 1:
        samples = samples.mean(axis=1).astype("int16")
    return samples, mp3_rate


def mp3_duration(data: bytes) -> float:
    """Exact duration via libsndfile; the constant-bitrate estimate if it can't decode MP3."""
    try:
        import soundfile as sf
        with sf.SoundFile(io.BytesIO(data)) as handle:
            return handle.frames / float(handle.samplerate)
    except Exception:  # noqa: BLE001 - libsndfile builds without MP3 raise various errors
        return len(data) * 8 / EDGE_MP3_BITS_PER_SECOND


def resample_linear(samples, src_rate: int, dst_rate: int):
    import numpy as np
    if src_rate == dst_rate or len(samples) == 0:
        return samples
    duration = len(samples) / float(src_rate)
    target = np.linspace(0.0, duration, int(round(duration * dst_rate)), endpoint=False)
    source = np.arange(len(samples)) / float(src_rate)
    return np.interp(target, source, samples.astype(np.float64)).astype(np.int16)


def prepare_for_listening(samples, rate: int, target_dbfs: float = -20.0, floor_dbfs: float = -50.0):
    """Trims leading/trailing silence and levels loudness, so neither gives a clip away.

    A louder clip tends to win a blind comparison, and a long lead-in is a
    fingerprint of the engine; both would bias the A/B picks.
    """
    import numpy as np
    x = samples.astype(np.float64) / 32768.0
    floor = 10 ** (floor_dbfs / 20)
    loud = np.flatnonzero(np.abs(x) > floor)
    if loud.size:
        pad = int(0.1 * rate)
        x = x[max(0, loud[0] - pad):min(len(x), loud[-1] + pad)]
    active = x[np.abs(x) > floor]
    if active.size:
        rms = float(np.sqrt(np.mean(active ** 2)))
        if rms > 0:
            x = x * (10 ** (target_dbfs / 20)) / rms
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    ceiling = 10 ** (-1.0 / 20)
    if peak > ceiling:
        x = x * ceiling / peak
    return (x * 32767).astype(np.int16)


# ---------------------------------------------------------------------------
# Gemini (Interactions API over REST)
# ---------------------------------------------------------------------------

class SseParser:
    """Incremental Server-Sent Events parser yielding each event's JSON payload."""

    def __init__(self):
        self._data: List[str] = []

    def feed(self, line: str) -> Optional[Dict[str, Any]]:
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            return None
        name, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if name == "event" and self._data:
            # A new event began without the blank line that should end the last
            # one; some line decoders drop empty lines.
            event = self._dispatch()
            return event
        if name == "data":
            self._data.append(value)
        return None

    def flush(self) -> Optional[Dict[str, Any]]:
        return self._dispatch()

    def _dispatch(self) -> Optional[Dict[str, Any]]:
        if not self._data:
            return None
        payload = "\n".join(self._data)
        self._data = []
        if payload.strip() == "[DONE]":
            return None
        try:
            event = json.loads(payload)
        except ValueError:
            return None
        return event if isinstance(event, dict) else None


def gemini_tts_request(text: str, voice: str, model: str = GEMINI_TTS_MODEL) -> Dict[str, Any]:
    """A streamed single-speaker request with no style prompt.

    The speech-generation guide's advice for voice agents: one call per turn as
    text arrives, and leave the style empty.
    """
    return {
        "model": model,
        "input": [{"type": "user_input", "content": [{"type": "text", "text": text}]}],
        "response_format": {"type": "audio", "mime_type": "audio/l16", "sample_rate": PCM_SAMPLE_RATE},
        "generation_config": {"speech_config": [{"voice": voice}]},
        "stream": True,
        "store": False,
    }


def gemini_audio(event: Dict[str, Any]) -> Optional[bytes]:
    if event.get("event_type") != "step.delta":
        return None
    delta = event.get("delta") or {}
    if delta.get("type") != "audio" or not delta.get("data"):
        return None
    return strip_riff(base64.b64decode(delta["data"]))


def gemini_usage(event: Dict[str, Any]) -> Dict[str, int]:
    """Text-in and audio-out token counts from an interaction.completed event."""
    if event.get("event_type") != "interaction.completed":
        return {}
    usage = (event.get("interaction") or {}).get("usage") or {}
    audio = sum(m.get("tokens", 0) for m in usage.get("output_tokens_by_modality") or [] if m.get("modality") == "audio")
    return {"input_tokens": int(usage.get("total_input_tokens") or 0), "audio_tokens": int(audio)}


def gemini_error(event: Dict[str, Any]) -> Optional[str]:
    if event.get("event_type") != "error":
        return None
    error = event.get("error") or {}
    return f"{error.get('code', 'error')}: {error.get('message', '')}".strip()


def gemini_voice_name(voice: str) -> str:
    """'en-US-Chirp3-HD-Aoede' -> 'Aoede'; Gemini TTS has the same 30 voices by bare name."""
    return chirp3_voice(voice) or voice


def transcription_request(audio: bytes, mime_type: str, model: str = TRANSCRIBE_MODEL) -> Dict[str, Any]:
    return {
        "model": model,
        "input": [{"type": "audio", "data": base64.b64encode(audio).decode("ascii"), "mime_type": mime_type}],
        "generation_config": {"transcription_config": {"language_codes": ["en-US"], "mode": {"type": "verbatim"}}},
        "store": False,
    }


def interaction_text(interaction: Dict[str, Any]) -> str:
    texts = []
    for step in interaction.get("steps") or []:
        if step.get("type") != "model_output":
            continue
        for content in step.get("content") or []:
            if content.get("type") == "text" and content.get("text"):
                texts.append(content["text"].strip())
    return " ".join(texts).strip()


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

class ProviderUnavailable(Exception):
    """The candidate can't run on this host (no credentials, no library): skipped, not failed."""


class ProviderHttpError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status


@dataclass
class Options:
    candidates: List[str] = field(default_factory=lambda: list(CANDIDATES))
    runs: int = 3
    voice: str = DEFAULT_VOICE
    edge_voice: str = EDGE_VOICE
    gemini_model: str = GEMINI_TTS_MODEL
    transcribe_model: str = TRANSCRIBE_MODEL
    rate: float = 1.0
    chunk_interval_s: float = 0.15
    prebuffer_s: float = 0.0
    timeout: float = 30.0
    reliability: int = 100
    pronunciation: bool = True
    chirp_endpoint: Optional[str] = None
    credentials_file: Optional[str] = None
    gemini_api_key: str = field(default="", repr=False)
    chirp_client: Any = field(default=None, repr=False)

    def public(self) -> Dict[str, Any]:
        """The settings worth recording with results -- never the API key."""
        keys = ("candidates", "runs", "voice", "edge_voice", "gemini_model", "transcribe_model", "rate",
                "chunk_interval_s", "prebuffer_s", "timeout", "reliability", "pronunciation", "chirp_endpoint")
        return {k: getattr(self, k) for k in keys}


@dataclass
class Synthesis:
    audio: bytearray = field(default_factory=bytearray)
    audio_format: str = "pcm"  # "pcm" (16-bit mono little-endian) or "mp3"
    sample_rate: int = PCM_SAMPLE_RATE
    chunks: List[AudioChunk] = field(default_factory=list)
    total_s: float = 0.0
    requests: int = 0
    usage: Dict[str, int] = field(default_factory=dict)
    measured_audio_s: Optional[float] = None

    @property
    def ttfa_s(self) -> Optional[float]:
        return self.chunks[0].arrival_s if self.chunks else None

    @property
    def audio_s(self) -> float:
        if self.measured_audio_s is not None:
            return self.measured_audio_s
        return sum(c.duration_s for c in self.chunks)

    def add_usage(self, usage: Dict[str, int]) -> None:
        for key, value in usage.items():
            self.usage[key] = self.usage.get(key, 0) + value


def edge_rate(rate: float) -> str:
    """1.2 -> '+20%', the rate format edge-tts takes (as TTSEngine builds it)."""
    percent = int(round((rate - 1.0) * 100))
    return f"{'+' if percent >= 0 else ''}{percent}%"


def voice_locale(voice: str) -> str:
    match = re.match(r"^([a-z]{2,3}-[A-Z]{2})-", voice or "")
    return match.group(1) if match else "en-US"


class Provider:
    name = ""
    bidirectional = False  # takes text while it is already speaking

    def __init__(self, opts: Options):
        self.opts = opts

    async def open(self) -> None:
        """Raises ProviderUnavailable when the candidate cannot run here."""

    async def close(self) -> None:
        pass

    def default_voice(self) -> str:
        return self.opts.voice

    async def synthesize(self, pieces: Sequence[str], schedule: Sequence[float],
                         voice: Optional[str] = None, rate: Optional[float] = None) -> Synthesis:
        raise NotImplementedError


class SentenceProvider(Provider):
    """An engine fed one sentence per request, as VoiceStream feeds edge-tts today."""

    async def synthesize(self, pieces, schedule, voice=None, rate=None) -> Synthesis:
        voice = voice or self.default_voice()
        rate = self.opts.rate if rate is None else rate
        synth = Synthesis()
        t0 = time.perf_counter()
        for text, at in group_into_sentences(pieces, schedule):
            wait = t0 + at - time.perf_counter()
            if wait > 0:
                await asyncio.sleep(wait)
            await self._request(text, voice, rate, t0, synth)
        synth.total_s = time.perf_counter() - t0
        return synth

    async def _request(self, text: str, voice: str, rate: float, t0: float, synth: Synthesis) -> None:
        raise NotImplementedError


class EdgeProvider(SentenceProvider):
    name = "edge"

    async def open(self) -> None:
        try:
            import edge_tts
        except ImportError as exc:
            raise ProviderUnavailable("edge-tts is not installed") from exc
        self._edge = edge_tts

    def default_voice(self) -> str:
        return self.opts.edge_voice

    async def _request(self, text, voice, rate, t0, synth) -> None:
        communicate = self._edge.Communicate(
            text, voice, rate=edge_rate(rate), receive_timeout=max(1, int(self.opts.timeout)),
        )
        synth.audio_format = "mp3"
        async for event in communicate.stream():
            if event.get("type") == "audio" and event.get("data"):
                data = event["data"]
                synth.chunks.append(AudioChunk(time.perf_counter() - t0, len(data) * 8 / EDGE_MP3_BITS_PER_SECOND))
                synth.audio.extend(data)
        synth.requests += 1


def make_chirp_client(opts: Options):
    """(TextToSpeechClient, texttospeech module), shared by both Chirp candidates.

    Raises ProviderUnavailable with the fix in the message: no key file is the
    expected state until the service account exists.
    """
    if opts.chirp_client is not None:
        return opts.chirp_client
    path = opts.credentials_file
    if not path:
        raise ProviderUnavailable(
            f"{CREDENTIALS_ENV_VAR} is not set. Create a service-account key for the GCP project "
            f"with the Text-to-Speech API enabled and point {CREDENTIALS_ENV_VAR} at it (see .env.example)."
        )
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise ProviderUnavailable(f"{CREDENTIALS_ENV_VAR} points at {path}, which does not exist.")
    if os.stat(path).st_mode & 0o077:
        print(f"warning: {path} is readable by other users; chmod 600 it.", file=sys.stderr)
    try:
        from google.cloud import texttospeech
        from google.oauth2 import service_account
    except ImportError as exc:
        raise ProviderUnavailable("google-cloud-texttospeech is not installed (pip install -r requirements.txt)") from exc
    try:
        credentials = service_account.Credentials.from_service_account_file(path, scopes=[CLOUD_PLATFORM_SCOPE])
    except (ValueError, KeyError) as exc:
        raise ProviderUnavailable(f"{path} is not a usable service-account key: {exc}") from exc
    client_options = {"api_endpoint": opts.chirp_endpoint} if opts.chirp_endpoint else None
    client = texttospeech.TextToSpeechClient(credentials=credentials, client_options=client_options)
    opts.chirp_client = (client, texttospeech)
    return opts.chirp_client


class ChirpUnaryProvider(SentenceProvider):
    name = "chirp-unary"

    async def open(self) -> None:
        self._client, self._tts = await asyncio.to_thread(make_chirp_client, self.opts)

    async def _request(self, text, voice, rate, t0, synth) -> None:
        tts = self._tts
        response = await asyncio.to_thread(
            self._client.synthesize_speech,
            input=tts.SynthesisInput(text=text),
            voice=tts.VoiceSelectionParams(language_code=voice_locale(voice), name=voice),
            audio_config=tts.AudioConfig(
                audio_encoding=tts.AudioEncoding.LINEAR16,
                sample_rate_hertz=PCM_SAMPLE_RATE,
                speaking_rate=rate,
            ),
            retry=None,  # one attempt per request, like the other candidates
            timeout=self.opts.timeout,
        )
        arrival = time.perf_counter() - t0
        content = response.audio_content
        pcm, sample_rate = wav_to_pcm(content) if content[:4] == b"RIFF" else (content, PCM_SAMPLE_RATE)
        synth.sample_rate = sample_rate
        synth.chunks.append(AudioChunk(arrival, len(pcm) / (2.0 * sample_rate)))
        synth.audio.extend(pcm)
        synth.requests += 1


class ChirpStreamProvider(Provider):
    name = "chirp-stream"
    bidirectional = True

    async def open(self) -> None:
        self._client, self._tts = await asyncio.to_thread(make_chirp_client, self.opts)

    async def synthesize(self, pieces, schedule, voice=None, rate=None) -> Synthesis:
        voice = voice or self.default_voice()
        rate = self.opts.rate if rate is None else rate
        return await asyncio.to_thread(self._stream, list(pieces), list(schedule), voice, rate)

    def _stream(self, pieces: List[str], schedule: List[float], voice: str, rate: float) -> Synthesis:
        tts = self._tts
        config = tts.StreamingSynthesizeConfig(
            voice=tts.VoiceSelectionParams(language_code=voice_locale(voice), name=voice),
            streaming_audio_config=tts.StreamingAudioConfig(
                audio_encoding=tts.AudioEncoding.PCM,
                sample_rate_hertz=PCM_SAMPLE_RATE,
                speaking_rate=rate,
            ),
        )
        clock: Dict[str, float] = {}

        def requests():
            # gRPC drains this iterator on its own thread, so sleeping here paces
            # the text without holding up the audio coming back.
            yield tts.StreamingSynthesizeRequest(streaming_config=config)
            for index, (piece, at) in enumerate(zip(pieces, schedule)):
                if index == 0:
                    clock["t0"] = time.perf_counter()
                else:
                    wait = clock["t0"] + at - time.perf_counter()
                    if wait > 0:
                        time.sleep(wait)
                yield tts.StreamingSynthesizeRequest(input=tts.StreamingSynthesisInput(text=piece))

        synth = Synthesis(requests=1)
        for response in self._client.streaming_synthesize(requests(), retry=None, timeout=self.opts.timeout):
            now = time.perf_counter()
            data = response.audio_content
            if not data:
                continue
            if not synth.chunks:
                data = strip_riff(data)
            synth.chunks.append(AudioChunk(now - clock.get("t0", now), len(data) / float(PCM_BYTES_PER_SECOND)))
            synth.audio.extend(data)
        synth.total_s = time.perf_counter() - clock.get("t0", time.perf_counter())
        return synth


class GeminiProvider(SentenceProvider):
    name = "gemini"

    async def open(self) -> None:
        if not self.opts.gemini_api_key:
            raise ProviderUnavailable("GEMINI_API_KEY is not set (.env).")
        import httpx
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.opts.timeout, connect=10.0))

    async def close(self) -> None:
        client = getattr(self, "_client", None)
        if client is not None:
            await client.aclose()

    async def _request(self, text, voice, rate, t0, synth) -> None:
        # Gemini TTS has no numeric speaking rate (only a style prompt), so
        # `rate` is not applied; see the note in the summary.
        body = gemini_tts_request(text, gemini_voice_name(voice), self.opts.gemini_model)
        parser = SseParser()
        headers = {"x-goog-api-key": self.opts.gemini_api_key}
        async with self._client.stream("POST", INTERACTIONS_URL, headers=headers, json=body) as response:
            if response.status_code != 200:
                detail = (await response.aread()).decode("utf-8", errors="replace")
                raise ProviderHttpError(response.status_code, _error_detail(detail))
            async for line in response.aiter_lines():
                event = parser.feed(line)
                if event is not None:
                    self._handle(event, t0, synth)
            event = parser.flush()
            if event is not None:
                self._handle(event, t0, synth)
        synth.requests += 1

    @staticmethod
    def _handle(event: Dict[str, Any], t0: float, synth: Synthesis) -> None:
        message = gemini_error(event)
        if message:
            raise RuntimeError(message)
        audio = gemini_audio(event)
        if audio:
            synth.chunks.append(AudioChunk(time.perf_counter() - t0, len(audio) / float(PCM_BYTES_PER_SECOND)))
            synth.audio.extend(audio)
            return
        usage = gemini_usage(event)
        if usage:
            synth.add_usage(usage)


PROVIDER_CLASSES = {
    "edge": EdgeProvider,
    "chirp-unary": ChirpUnaryProvider,
    "chirp-stream": ChirpStreamProvider,
    "gemini": GeminiProvider,
}


def _error_detail(body: str) -> str:
    try:
        error = json.loads(body).get("error") or {}
        message = error.get("message") or body
    except (ValueError, AttributeError):
        message = body
    return " ".join(str(message).split())[:300]


def classify_error(exc: BaseException) -> str:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    if isinstance(exc, ProviderHttpError):
        return f"http_{exc.status}"
    name = type(exc).__name__
    if name in ("DeadlineExceeded", "ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout", "TimeoutException"):
        return "timeout"
    return name


class Transcriber:
    """The transcription model (core.util.models.TRANSCRIBE) over the Interactions API, for the pronunciation proxy."""

    def __init__(self, api_key: str, model: str = TRANSCRIBE_MODEL, timeout: float = 60.0):
        import httpx
        self._key = api_key
        self._model = model
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0))
        self.usage = {"audio_tokens": 0, "output_tokens": 0}

    async def transcribe(self, audio: bytes, mime_type: str) -> str:
        response = await self._client.post(
            INTERACTIONS_URL,
            headers={"x-goog-api-key": self._key},
            json=transcription_request(audio, mime_type, self._model),
        )
        if response.status_code != 200:
            raise ProviderHttpError(response.status_code, _error_detail(response.text))
        interaction = response.json()
        usage = interaction.get("usage") or {}
        self.usage["audio_tokens"] += sum(
            m.get("tokens", 0) for m in usage.get("input_tokens_by_modality") or [] if m.get("modality") == "audio"
        )
        self.usage["output_tokens"] += int(usage.get("total_output_tokens") or 0)
        return interaction_text(interaction)

    async def close(self) -> None:
        await self._client.aclose()


async def timed_synthesis(provider: Provider, pieces: Sequence[str], schedule: Sequence[float],
                          voice: Optional[str], rate: Optional[float], timeout: float):
    """(Synthesis, None) or (None, exception): every failure is a data point here."""
    try:
        synth = await asyncio.wait_for(provider.synthesize(pieces, schedule, voice, rate), timeout)
    except Exception as exc:  # noqa: BLE001
        return None, exc
    if not synth.chunks:
        return None, RuntimeError("no audio returned")
    if synth.audio_format == "mp3":
        synth.measured_audio_s = mp3_duration(bytes(synth.audio))
    return synth, None


def audio_for_transcription(synth: Synthesis) -> Tuple[bytes, str]:
    if synth.audio_format == "mp3":
        return bytes(synth.audio), "audio/mp3"
    return pcm_to_wav(bytes(synth.audio), synth.sample_rate), "audio/wav"


def save_audio(out_dir: str, candidate: str, utterance_id: str, synth: Synthesis) -> str:
    """Writes the clip and returns its path relative to `out_dir`."""
    extension = "mp3" if synth.audio_format == "mp3" else "wav"
    relative = os.path.join("audio", candidate, f"{utterance_id}.{extension}")
    path = os.path.join(out_dir, relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = bytes(synth.audio) if extension == "mp3" else pcm_to_wav(bytes(synth.audio), synth.sample_rate)
    with open(path, "wb") as handle:
        handle.write(data)
    return relative


# ---------------------------------------------------------------------------
# Test set and agents
# ---------------------------------------------------------------------------

def load_test_set(path: str = UTTERANCES_PATH) -> Dict[str, Any]:
    """The data file, validated: unique ids, text or chunks, and terms that occur in the text."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    seen = set()
    for utterance in data["utterances"]:
        uid = utterance.get("id")
        if not uid or uid in seen:
            raise ValueError(f"utterance id missing or duplicated: {uid!r}")
        seen.add(uid)
        if utterance.get("chunks"):
            utterance["text"] = "".join(utterance["chunks"]).strip()
        if not utterance.get("text") or not utterance.get("category"):
            raise ValueError(f"{uid}: needs a category and text or chunks")
        utterance.setdefault("terms", [])
        for term in utterance["terms"]:
            if not term_found([term], normalize_words(utterance["text"])):
                raise ValueError(f"{uid}: term {term!r} does not occur in its text")
    data.setdefault("term_alternates", {})
    data.setdefault("audition_lines", {})
    return data


def select_utterances(utterances: Sequence[Dict[str, Any]], ids: Optional[Sequence[str]] = None,
                      categories: Optional[Sequence[str]] = None, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    chosen = list(utterances)
    if ids:
        unknown = set(ids) - {u["id"] for u in utterances}
        if unknown:
            raise ValueError(f"unknown utterance ids: {', '.join(sorted(unknown))}")
        chosen = [u for u in chosen if u["id"] in ids]
    if categories:
        chosen = [u for u in chosen if u["category"] in categories]
    return stratified_selection(chosen, limit)


def audition_targets(agents_dir: str = AGENTS_DIR, lines: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """One entry per voice-capable agent: its voice (agent.json, else DEFAULT_VOICE) and a line."""
    targets = []
    for agent_id in sorted(os.listdir(agents_dir)):
        if agent_id in AUDITION_EXCLUDED_AGENTS:
            continue
        path = os.path.join(agents_dir, agent_id, "agent.json")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                config = json.load(handle)
        except (OSError, ValueError) as exc:
            print(f"warning: skipping {agent_id}: unreadable agent.json ({exc})", file=sys.stderr)
            continue
        block = config.get("voice") if isinstance(config.get("voice"), dict) else {}
        voice = block.get("name") or DEFAULT_VOICE
        note = ""
        if not chirp3_voice(voice):
            note = f"agent.json names unknown voice {voice!r}; auditioned in {DEFAULT_VOICE}"
            voice = DEFAULT_VOICE
        display = config.get("name") or agent_id
        targets.append({
            "agent": agent_id,
            "display_name": display,
            "voice": voice,
            "rate": block.get("rate"),
            "source": "agent.json" if block.get("name") and not note else "default",
            "line": (lines or {}).get(agent_id) or f"Hi, this is {display}. This is how I sound.",
            "note": note,
        })
    return targets


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------

async def open_providers(opts: Options) -> Tuple[List[Provider], Dict[str, str]]:
    providers, skipped = [], {}
    for name in opts.candidates:
        provider = PROVIDER_CLASSES[name](opts)
        try:
            await provider.open()
        except ProviderUnavailable as exc:
            skipped[name] = str(exc)
            print(f"skip {name}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 - a broken candidate must not stop the others
            skipped[name] = f"failed to start: {type(exc).__name__}: {exc}"
            print(f"skip {name}: {skipped[name]}")
            continue
        providers.append(provider)
    return providers, skipped


def synthesis_record(candidate: str, utterance: Dict[str, Any], run: int, synth: Optional[Synthesis],
                     exc: Optional[BaseException], prebuffer_s: float) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "candidate": candidate,
        "utterance": utterance["id"],
        "category": utterance["category"],
        "run": run,
        "chars": len(utterance["text"]),
        "ok": synth is not None,
    }
    if synth is None:
        record["error_kind"] = classify_error(exc)
        record["error"] = " ".join(str(exc).split())[:300] or type(exc).__name__
        return record
    playback = simulate_playback(synth.chunks, prebuffer_s)
    audio_s = synth.audio_s
    record.update({
        "ttfa_ms": round(synth.ttfa_s * 1000, 1),
        "total_ms": round(synth.total_s * 1000, 1),
        "audio_s": round(audio_s, 3),
        "rtf": round(synth.total_s / audio_s, 3) if audio_s else None,
        "chunks": len(synth.chunks),
        "requests": synth.requests,
        "underruns": playback.underruns,
        "stall_ms": round(playback.stall_s * 1000, 1),
        "usage": dict(synth.usage),
    })
    return record


def _rotate(items: List[Any], k: int) -> List[Any]:
    if not items:
        return items
    k %= len(items)
    return items[k:] + items[:k]


async def run_synthesis_rounds(opts: Options, providers: List[Provider], utterances: List[Dict[str, Any]],
                               out_dir: str) -> Tuple[List[Dict[str, Any]], Dict[Tuple[str, str], Synthesis], Dict[str, Dict[str, str]]]:
    records: List[Dict[str, Any]] = []
    first_audio: Dict[Tuple[str, str], Synthesis] = {}
    audio_files: Dict[str, Dict[str, str]] = {p.name: {} for p in providers}
    total = opts.runs * len(utterances) * len(providers)
    done = 0
    for run in range(1, opts.runs + 1):
        for index, utterance in enumerate(utterances):
            pieces, schedule = utterance_pieces(utterance, opts.chunk_interval_s)
            # Rotating the order spreads any drift in network conditions evenly.
            for provider in _rotate(providers, run + index):
                synth, exc = await timed_synthesis(provider, pieces, schedule, None, None, opts.timeout)
                record = synthesis_record(provider.name, utterance, run, synth, exc, opts.prebuffer_s)
                records.append(record)
                done += 1
                print(_progress_line(done, total, record))
                key = (provider.name, utterance["id"])
                if synth is not None and key not in first_audio:
                    first_audio[key] = synth
                    audio_files[provider.name][utterance["id"]] = save_audio(out_dir, provider.name, utterance["id"], synth)
    return records, first_audio, audio_files


def _progress_line(done: int, total: int, record: Dict[str, Any]) -> str:
    head = f"[{done:>3}/{total}] run {record['run']} {record['utterance']:<22} {record['candidate']:<13}"
    if not record["ok"]:
        return f"{head} ERROR {record['error_kind']}: {record['error'][:120]}"
    return (f"{head} ttfa {record['ttfa_ms']:>6.0f} ms  audio {record['audio_s']:>5.1f} s  "
            f"rtf {record['rtf']:.2f}  underruns {record['underruns']}")


async def run_pronunciation(opts: Options, utterances: List[Dict[str, Any]], first_audio: Dict[Tuple[str, str], Synthesis],
                            alternates: Dict[str, List[str]]) -> Dict[str, Any]:
    transcriber = Transcriber(opts.gemini_api_key, opts.transcribe_model, timeout=max(60.0, opts.timeout))
    by_id = {u["id"]: u for u in utterances}
    items: Dict[str, List[Dict[str, Any]]] = {}
    try:
        for (candidate, utterance_id), synth in sorted(first_audio.items()):
            utterance = by_id[utterance_id]
            audio, mime_type = audio_for_transcription(synth)
            entry: Dict[str, Any] = {"utterance": utterance_id}
            try:
                transcript = await transcriber.transcribe(audio, mime_type)
            except Exception as exc:  # noqa: BLE001
                entry["error"] = f"{classify_error(exc)}: {exc}"[:300]
                print(f"transcribe {candidate:<13} {utterance_id:<22} ERROR {entry['error']}")
            else:
                entry["transcript"] = transcript
                entry.update(score_pronunciation(utterance["text"], transcript, utterance["terms"], alternates))
                missed = ", ".join(entry["missed_terms"]) or "-"
                print(f"transcribe {candidate:<13} {utterance_id:<22} wer {entry['wer']:.2f}  missed: {missed}")
            items.setdefault(candidate, []).append(entry)
    finally:
        await transcriber.close()
    return {"model": opts.transcribe_model, "usage": dict(transcriber.usage),
            "candidates": summarize_pronunciation(items)}


def summarize_pronunciation(items: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Per candidate: pooled WER, term misses, and misses scaled to a 25-utterance set."""
    summary: Dict[str, Any] = {}
    for candidate, entries in items.items():
        scored = [e for e in entries if "transcript" in e]
        ref_words = sum(e["ref_words"] for e in scored)
        errors = sum(e["substitutions"] + e["deletions"] + e["insertions"] for e in scored)
        terms = sum(e["terms"] for e in scored)
        misses = [(e["utterance"], t) for e in scored for t in e["missed_terms"]]
        summary[candidate] = {
            "utterances": len(scored),
            "errors": len(entries) - len(scored),
            "wer": round(errors / ref_words, 4) if ref_words else None,
            "terms": terms,
            "term_misses": len(misses),
            "term_misses_per_25": round(len(misses) * 25 / len(scored), 2) if scored else None,
            "missed": [f"{u}: {t}" for u, t in misses],
            "items": entries,
        }
    return summary


def rescore_run(run_dir: str, test_set: Dict[str, Any]) -> Dict[str, Any]:
    """Re-scores a run's stored transcripts against the current data file; no API calls.

    For after adding an alternate spelling: the transcripts don't change, only
    which of them count as misses.
    """
    path = os.path.join(run_dir, "results.json")
    with open(path, encoding="utf-8") as handle:
        results = json.load(handle)
    pronunciation = results.get("pronunciation")
    if not pronunciation:
        raise SystemExit(f"{run_dir} has no pronunciation results to re-score.")
    by_id = {u["id"]: u for u in test_set["utterances"]}
    items: Dict[str, List[Dict[str, Any]]] = {}
    for candidate, summary in pronunciation["candidates"].items():
        for entry in summary["items"]:
            utterance = by_id.get(entry["utterance"])
            if utterance and "transcript" in entry:
                entry = {"utterance": entry["utterance"], "transcript": entry["transcript"],
                         **score_pronunciation(utterance["text"], entry["transcript"], utterance["terms"],
                                               test_set["term_alternates"])}
            items.setdefault(candidate, []).append(entry)
    pronunciation["candidates"] = summarize_pronunciation(items)
    write_results(run_dir, results)
    for candidate, summary in pronunciation["candidates"].items():
        print(f"{candidate:<13} term misses {summary['term_misses']} of {summary['terms']}, "
              f"wer {summary['wer']}: {'; '.join(summary['missed']) or 'none'}")
    return results


async def run_reliability(opts: Options, providers: List[Provider], pool: List[Dict[str, Any]]) -> Dict[str, Any]:
    results: Dict[str, Any] = {}
    for provider in providers:
        outcomes: List[Dict[str, Any]] = []
        for i in range(opts.reliability):
            utterance = pool[i % len(pool)]
            synth, exc = await timed_synthesis(provider, [utterance["text"]], [0.0], None, None, opts.timeout)
            outcomes.append({"ok": synth is not None, "ttfa_ms": round(synth.ttfa_s * 1000, 1) if synth else None,
                             "kind": None if synth else classify_error(exc),
                             "error": None if synth else str(exc)[:200]})
            if (i + 1) % 10 == 0 or i + 1 == opts.reliability:
                failed = sum(1 for o in outcomes if not o["ok"])
                print(f"reliability {provider.name:<13} {i + 1:>4}/{opts.reliability}  failures {failed}")
        kinds: Dict[str, int] = {}
        for outcome in outcomes:
            if outcome["kind"]:
                kinds[outcome["kind"]] = kinds.get(outcome["kind"], 0) + 1
        ttfa = [o["ttfa_ms"] for o in outcomes if o["ok"]]
        results[provider.name] = {
            "requests": len(outcomes),
            "failures": sum(kinds.values()),
            "timeouts": kinds.get("timeout", 0),
            "kinds": kinds,
            "first_errors": [o["error"] for o in outcomes if not o["ok"]][:5],
            "ttfa_ms": {"p50": _round(percentile(ttfa, 50)), "p90": _round(percentile(ttfa, 90)), "n": len(ttfa)},
        }
    return results


def _round(value: Optional[float], digits: int = 1) -> Optional[float]:
    return None if value is None else round(value, digits)


def aggregate(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    ok = [r for r in records if r["ok"]]
    ttfa = [r["ttfa_ms"] for r in ok]
    rtf = [r["rtf"] for r in ok if r.get("rtf") is not None]
    by_category: Dict[str, Any] = {}
    for category in dict.fromkeys(r["category"] for r in records):
        values = [r["ttfa_ms"] for r in ok if r["category"] == category]
        by_category[category] = {"ttfa_p50_ms": _round(percentile(values, 50)), "n": len(values)}
    kinds: Dict[str, int] = {}
    for record in records:
        if not record["ok"]:
            kinds[record["error_kind"]] = kinds.get(record["error_kind"], 0) + 1
    return {
        "syntheses": len(records),
        "failures": len(records) - len(ok),
        "failure_kinds": kinds,
        "ttfa_ms": {"p50": _round(percentile(ttfa, 50)), "p90": _round(percentile(ttfa, 90)), "n": len(ttfa)},
        "underruns": sum(r["underruns"] for r in ok),
        "syntheses_with_underruns": sum(1 for r in ok if r["underruns"]),
        "stall_ms_total": round(sum(r["stall_ms"] for r in ok), 1),
        "rtf": {"p50": _round(percentile(rtf, 50), 3), "p90": _round(percentile(rtf, 90), 3),
                "max": _round(max(rtf), 3) if rtf else None},
        "by_category": by_category,
    }


def cost_estimates(candidates: List[str], records: List[Dict[str, Any]]) -> Dict[str, Any]:
    estimates: Dict[str, Any] = {}
    for candidate in candidates:
        ok = [r for r in records if r["candidate"] == candidate and r["ok"]]
        chars = sum(r["chars"] for r in ok)
        if candidate == "edge":
            estimates[candidate] = {"monthly_usd": 0.0, "note": "free, but unofficial: no account, no SLA"}
        elif candidate.startswith("chirp"):
            estimates[candidate] = {
                "monthly_usd": chirp_monthly_cost(MONTHLY_SPOKEN_CHARS),
                "monthly_usd_without_free_tier": chirp_monthly_cost(MONTHLY_SPOKEN_CHARS, free_chars=0),
                "run_chars": chars,
                "note": f"{MONTHLY_SPOKEN_CHARS:,} of {CHIRP_FREE_CHARS_PER_MONTH:,} free characters a month",
            }
        elif candidate == "gemini":
            audio_s = sum(r["audio_s"] for r in ok)
            input_tokens = sum(r.get("usage", {}).get("input_tokens", 0) for r in ok)
            audio_tokens = sum(r.get("usage", {}).get("audio_tokens", 0) for r in ok)
            measured = bool(chars and audio_s and input_tokens)
            seconds_per_char = audio_s / chars if measured else 1 / NOMINAL_CHARS_PER_AUDIO_SECOND
            tokens_per_char = input_tokens / chars if measured else 1 / NOMINAL_CHARS_PER_TEXT_TOKEN
            monthly = {label: round(gemini_monthly_cost(MONTHLY_SPOKEN_CHARS, seconds_per_char, tokens_per_char, pin, pout), 2)
                       for label, pin, pout in GEMINI_TTS_PRICES}
            run_usd = (input_tokens * GEMINI_TTS_PRICES[0][1] + audio_tokens * GEMINI_TTS_PRICES[0][2]) / 1e6
            estimates[candidate] = {
                "monthly_usd": monthly[GEMINI_TTS_PRICES[0][0]],
                "monthly_usd_by_period": monthly,
                "basis": "measured" if measured else "nominal",
                "chars_per_audio_second": round(1 / seconds_per_char, 2),
                "chars_per_text_token": round(1 / tokens_per_char, 2),
                "run_tokens": {"input": input_tokens, "audio": audio_tokens},
                "run_usd": round(run_usd, 4),
                "note": "no free tier at production volume; billed per text and audio token",
            }
    return estimates


def evaluate(results: Dict[str, Any]) -> Dict[str, Dict[str, Dict[str, str]]]:
    """metric -> candidate -> {"value", "status"} with status pass, fail or n/a."""
    table: Dict[str, Dict[str, Dict[str, str]]] = {}

    def put(metric, candidate, value, status):
        table.setdefault(metric, {})[candidate] = {"value": value, "status": status}

    pronunciation = (results.get("pronunciation") or {}).get("candidates", {})
    naturalness = results.get("naturalness") or {}
    reliability = results.get("reliability") or {}
    for candidate in results["ran"]:
        agg = results["aggregates"].get(candidate) or {}
        ttfa = agg.get("ttfa_ms") or {}
        if ttfa.get("p50") is not None:
            put("ttfa", candidate, f"{ttfa['p50']:.0f} / {ttfa['p90']:.0f} ms",
                "pass" if ttfa["p50"] <= TARGET_TTFA_P50_MS else "fail")
        else:
            put("ttfa", candidate, "no data", "n/a")
        if agg.get("ttfa_ms", {}).get("n"):
            put("underruns", candidate, f"{agg['underruns']} ({agg['syntheses_with_underruns']} of {agg['ttfa_ms']['n']} clips)",
                "pass" if agg["underruns"] == 0 else "fail")
            rtf = agg["rtf"]
            put("rtf", candidate, f"{rtf['p50']:.2f} / {rtf['p90']:.2f} / {rtf['max']:.2f}",
                "pass" if rtf["p90"] < TARGET_RTF else "fail")
        else:
            put("underruns", candidate, "no data", "n/a")
            put("rtf", candidate, "no data", "n/a")
        p = pronunciation.get(candidate)
        if p and p.get("utterances"):
            put("terms", candidate, f"{p['term_misses']} of {p['terms']} (≈{p['term_misses_per_25']:.1f} per 25)",
                "pass" if p["term_misses_per_25"] <= TARGET_TERM_MISSES_PER_25 else "fail")
            put("wer", candidate, f"{100 * p['wer']:.1f}%", "n/a")
        else:
            put("terms", candidate, "not run", "n/a")
            put("wer", candidate, "not run", "n/a")
        if candidate == BASELINE:
            put("naturalness", candidate, "baseline", "n/a")
        elif candidate in naturalness and naturalness[candidate].get("preference") is not None:
            n = naturalness[candidate]
            answered = n["preferred"] + n["baseline"] + n["ties"]
            put("naturalness", candidate, f"{100 * n['preference']:.0f}% of {answered} pairs",
                "pass" if n["preference"] >= TARGET_PREFERENCE else "fail")
        else:
            put("naturalness", candidate, "awaiting your picks", "n/a")
        r = reliability.get(candidate)
        if r:
            put("reliability", candidate, f"{r['failures']} of {r['requests']}" + (f" ({r['timeouts']} timeouts)" if r["timeouts"] else ""),
                "pass" if r["failures"] == 0 else "fail")
        else:
            put("reliability", candidate, "not run", "n/a")
        c = (results.get("cost") or {}).get(candidate)
        if c is not None:
            if candidate == "gemini":
                periods = c["monthly_usd_by_period"]
                value = " / ".join(f"${v:.2f} {k}" for k, v in periods.items())
            else:
                value = f"${c['monthly_usd']:.2f}"
            put("cost", candidate, value, "pass" if c["monthly_usd"] == 0 else "fail")
    return table


METRIC_ROWS = (
    ("ttfa", "Time to first audio p50 / p90", f"≤ {TARGET_TTFA_P50_MS:.0f} ms p50 (streaming)"),
    ("underruns", "Playback underruns", "0"),
    ("rtf", "Synthesis ÷ audio (RTF) p50 / p90 / max", f"p90 < {TARGET_RTF}"),
    ("terms", "Term misses (pronunciation proxy)", f"≤ {TARGET_TERM_MISSES_PER_25:.0f} per 25"),
    ("wer", "Word error rate (pronunciation proxy)", "for comparison"),
    ("naturalness", "Preferred over edge-tts (blind A/B)", f"≥ {100 * TARGET_PREFERENCE:.0f}%"),
    ("reliability", "Failures (errors + timeouts)", "0"),
    ("cost", f"Cost at {MONTHLY_SPOKEN_CHARS // 1000}k chars / month", "inside the free tier"),
)

_STATUS_MARK = {"pass": "✅", "fail": "❌", "n/a": ""}


def render_summary(results: Dict[str, Any]) -> str:
    table = evaluate(results)
    ran = results["ran"]
    cfg = results["config"]
    lines = [
        f"# TTS benchmark — {results['started']}",
        "",
        f"Host: {results['host']['platform']} ({results['host']['machine']}, {results['host']['cpus']} CPUs), "
        f"Python {results['host']['python']}.",
        f"{len(results['utterances'])} utterances × {cfg['runs']} runs; clause chunks every "
        f"{1000 * cfg['chunk_interval_s']:.0f} ms; speaking rate {cfg['rate']}; voices: edge {cfg['edge_voice']}, "
        f"Chirp {cfg['voice']}, Gemini {gemini_voice_name(cfg['voice'])} ({cfg['gemini_model']}).",
        "",
        "| Metric | Target | " + " | ".join(ran) + " |",
        "|---|---|" + "---|" * len(ran),
    ]
    for key, label, target in METRIC_ROWS:
        cells = []
        for candidate in ran:
            cell = table.get(key, {}).get(candidate)
            cells.append("—" if cell is None else f"{cell['value']} {_STATUS_MARK[cell['status']]}".strip())
        lines.append(f"| {label} | {target} | " + " | ".join(cells) + " |")
    lines.append("")
    if results.get("skipped"):
        lines.append("**Skipped:**")
        lines.extend(f"- `{name}`: {reason}" for name, reason in results["skipped"].items())
        lines.append("")
    categories = list(dict.fromkeys(u["category"] for u in results["utterances"]))
    lines += ["### TTFA p50 by kind of reply (ms)", "",
              "| Kind | " + " | ".join(ran) + " |", "|---|" + "---|" * len(ran)]
    for category in categories:
        cells = []
        for candidate in ran:
            value = results["aggregates"].get(candidate, {}).get("by_category", {}).get(category, {}).get("ttfa_p50_ms")
            cells.append("—" if value is None else f"{value:.0f}")
        lines.append(f"| {category} | " + " | ".join(cells) + " |")
    cold = results.get("cold_start_ms") or {}
    if cold:
        lines += ["", "Cold start (first call, excluded above): " +
                  ", ".join(f"{k} {v:.0f} ms" if v is not None else f"{k} failed" for k, v in cold.items()) + "."]
    pronunciation = (results.get("pronunciation") or {}).get("candidates", {})
    if pronunciation:
        lines += ["", "### Term misses", ""]
        for candidate, p in pronunciation.items():
            lines.append(f"- `{candidate}`: " + ("; ".join(p["missed"]) if p["missed"] else "none"))
        lines.append("")
        lines.append("A miss is the term missing from the transcript: a mispronunciation, or a spelling the "
                     "transcriber chose. Transcripts are in results.json.")
    reliability = results.get("reliability") or {}
    if reliability:
        lines += ["", "### Reliability", ""]
        for candidate, r in reliability.items():
            kinds = ", ".join(f"{k} ×{v}" for k, v in r["kinds"].items()) or "none"
            ttfa = r["ttfa_ms"]
            timing = (f" Short-reply TTFA p50 {ttfa['p50']:.0f} ms, p90 {ttfa['p90']:.0f} ms."
                      if ttfa.get("p50") is not None else "")
            lines.append(f"- `{candidate}`: {r['failures']} failures in {r['requests']} requests ({kinds}).{timing}")
    cost = results.get("cost") or {}
    if cost:
        lines += ["", "### Cost", ""]
        for candidate, c in cost.items():
            extra = ""
            if candidate == "gemini":
                extra = (f" Basis: {c['basis']} ({c['chars_per_audio_second']} chars per audio second, "
                         f"{c['chars_per_text_token']} chars per text token). This run: ${c['run_usd']:.4f}.")
            elif candidate.startswith("chirp"):
                extra = f" Without the free tier: ${c['monthly_usd_without_free_tier']:.2f}."
            lines.append(f"- `{candidate}`: {c['note']}.{extra}")
    notes = ["Gemini TTS has no numeric speaking rate, so `--rate` applies to edge and Chirp only."]
    for candidate in ran:
        kinds = (results["aggregates"].get(candidate) or {}).get("failure_kinds") or {}
        if kinds:
            first = next((r.get("error") for r in results.get("records", [])
                          if r["candidate"] == candidate and not r["ok"]), "")
            notes.append(f"`{candidate}` failed {sum(kinds.values())} of "
                         f"{results['aggregates'][candidate]['syntheses']} syntheses "
                         f"({', '.join(f'{k} ×{v}' for k, v in kinds.items())}); first error: {first[:160]}")
    if results.get("pairs_dir"):
        notes.append(f"Blind A/B pairs: `{results['pairs_dir']}` (see README.md there); score with "
                     f"`python scripts/tts_benchmark.py --score-pairs {results['pairs_dir']}`.")
    if results.get("pairs_skipped"):
        notes.append(f"No A/B pairs: {results['pairs_skipped']}")
    lines += ["", "### Notes", ""] + [f"- {n}" for n in notes]
    return "\n".join(lines) + "\n"


def host_info() -> Dict[str, Any]:
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpus": os.cpu_count(),
        "python": platform.python_version(),
    }


def write_results(out_dir: str, results: Dict[str, Any]) -> None:
    with open(os.path.join(out_dir, "results.json"), "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2, ensure_ascii=False)
    with open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8") as handle:
        handle.write(render_summary(results))


async def run_benchmark(opts: Options, test_set: Dict[str, Any], utterances: List[Dict[str, Any]],
                        out_dir: str, ab_pairs: bool = False, pairs_seed: int = DEFAULT_PAIRS_SEED) -> Dict[str, Any]:
    os.makedirs(out_dir, exist_ok=True)
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    providers, skipped = await open_providers(opts)
    if not providers:
        raise SystemExit("No candidate can run here; see the skip messages above.")
    try:
        cold: Dict[str, Optional[float]] = {}
        for provider in providers:
            synth, exc = await timed_synthesis(provider, [WARMUP_TEXT], [0.0], None, None, opts.timeout)
            cold[provider.name] = round(synth.ttfa_s * 1000, 1) if synth else None
            print(f"warm-up {provider.name:<13} " + (f"cold ttfa {cold[provider.name]:.0f} ms" if synth else f"FAILED {exc}"))
        records, first_audio, audio_files = await run_synthesis_rounds(opts, providers, utterances, out_dir)

        pronunciation = None
        if opts.pronunciation and opts.gemini_api_key and first_audio:
            pronunciation = await run_pronunciation(opts, utterances, first_audio, test_set["term_alternates"])
        elif opts.pronunciation:
            print("skip pronunciation: needs GEMINI_API_KEY for the transcriber")

        reliability = None
        if opts.reliability > 0:
            pool = [u for u in test_set["utterances"] if u["category"] in ("ack", "numbers", "names")]
            reliability = await run_reliability(opts, providers, pool)
    finally:
        for provider in providers:
            await provider.close()

    ran = [p.name for p in providers]
    results: Dict[str, Any] = {
        "started": started,
        "host": host_info(),
        "config": opts.public(),
        "ran": ran,
        "skipped": skipped,
        "utterances": [{k: u[k] for k in ("id", "agent", "category", "text", "terms") if k in u} for u in utterances],
        "cold_start_ms": cold,
        "aggregates": {name: aggregate([r for r in records if r["candidate"] == name]) for name in ran},
        "pronunciation": pronunciation,
        "reliability": reliability,
        "cost": cost_estimates(ran, records),
        "naturalness": None,
        "audio_files": audio_files,
        "records": records,
    }
    write_results(out_dir, results)
    if ab_pairs:
        try:
            results["pairs_dir"] = build_pairs_from_run(out_dir, seed=pairs_seed)
        except PairsUnavailable as exc:
            results["pairs_skipped"] = str(exc)
            print(f"skip A/B pairs: {exc}")
        write_results(out_dir, results)
    return results


# ---------------------------------------------------------------------------
# Blind A/B pairs
# ---------------------------------------------------------------------------

def assign_pairs(items: Sequence[Tuple[str, str]], seed: int) -> List[Dict[str, str]]:
    """Shuffles (utterance, candidate) items and puts the candidate on side A or B at random.

    Deterministic for a seed, so a key can always be regenerated and checked.
    """
    rng = random.Random(seed)
    shuffled = list(items)
    rng.shuffle(shuffled)
    width = max(2, len(str(len(shuffled))))
    pairs = []
    for number, (utterance, candidate) in enumerate(shuffled, 1):
        a, b = (candidate, BASELINE) if rng.random() < 0.5 else (BASELINE, candidate)
        pairs.append({"pair": f"{number:0{width}d}", "utterance": utterance, "A": a, "B": b})
    return pairs


_TIE_PICKS = {"=", "TIE", "T", "SAME", "EQUAL"}


def score_picks(picks: Dict[str, str], key: Sequence[Dict[str, str]]) -> Dict[str, Dict[str, Any]]:
    """Per candidate: how often it beat edge-tts. Ties count against it; blanks are left out."""
    scores: Dict[str, Dict[str, Any]] = {}
    for entry in key:
        candidate = entry["A"] if entry["A"] != BASELINE else entry["B"]
        score = scores.setdefault(candidate, {"pairs": 0, "preferred": 0, "baseline": 0, "ties": 0, "unanswered": 0})
        score["pairs"] += 1
        pick = (picks.get(entry["pair"]) or "").strip().upper()
        if pick in ("A", "B"):
            score["preferred" if entry[pick] == candidate else "baseline"] += 1
        elif pick in _TIE_PICKS:
            score["ties"] += 1
        else:
            score["unanswered"] += 1
    for score in scores.values():
        answered = score["preferred"] + score["baseline"] + score["ties"]
        score["preference"] = round(score["preferred"] / answered, 3) if answered else None
        score["status"] = ("n/a" if score["preference"] is None
                           else "pass" if score["preference"] >= TARGET_PREFERENCE else "fail")
    return scores


def pair_candidates(available: Sequence[str]) -> List[str]:
    """Everything but the baseline; Chirp once, as the streaming variant the plan would ship."""
    chosen = [c for c in available if c != BASELINE]
    if "chirp-stream" in chosen and "chirp-unary" in chosen:
        chosen.remove("chirp-unary")
    return chosen


class PairsUnavailable(Exception):
    """Nothing to pair: edge-tts or every candidate produced no audio in that run."""


def build_pairs_from_run(run_dir: str, seed: int = DEFAULT_PAIRS_SEED, limit: Optional[int] = None) -> str:
    """Writes pairs/ (clips + picks.csv + README.md) and ab_key/answer_key.json; returns pairs/."""
    with open(os.path.join(run_dir, "results.json"), encoding="utf-8") as handle:
        results = json.load(handle)
    files = {name: clips for name, clips in (results.get("audio_files") or {}).items() if clips}
    if BASELINE not in files:
        raise PairsUnavailable(f"{run_dir} has no {BASELINE} audio to compare against.")
    texts = {u["id"]: u["text"] for u in results["utterances"]}
    items = []
    for candidate in pair_candidates(list(files)):
        shared = [u for u in texts if u in files[candidate] and u in files[BASELINE]]
        items.extend((u, candidate) for u in shared[:limit])
    if not items:
        raise PairsUnavailable("No utterance has audio from both edge-tts and a candidate.")
    pairs = assign_pairs(items, seed)
    pairs_dir = os.path.join(run_dir, "pairs")
    key_dir = os.path.join(run_dir, "ab_key")
    os.makedirs(pairs_dir, exist_ok=True)
    os.makedirs(key_dir, exist_ok=True)
    for entry in pairs:
        for side in ("A", "B"):
            path = os.path.join(run_dir, files[entry[side]][entry["utterance"]])
            with open(path, "rb") as handle:
                data = handle.read()
            samples, rate = decode_audio(data, "mp3" if path.endswith(".mp3") else "wav")
            samples = resample_linear(samples, rate, PCM_SAMPLE_RATE)
            clip = prepare_for_listening(samples, PCM_SAMPLE_RATE)
            with open(os.path.join(pairs_dir, f"pair_{entry['pair']}_{side}.wav"), "wb") as handle:
                handle.write(pcm_to_wav(clip.tobytes(), PCM_SAMPLE_RATE))
    with open(os.path.join(pairs_dir, "picks.csv"), "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["pair", "utterance", "text", "pick", "notes"])
        for entry in pairs:
            writer.writerow([entry["pair"], entry["utterance"], texts[entry["utterance"]], "", ""])
    with open(os.path.join(key_dir, "answer_key.json"), "w", encoding="utf-8") as handle:
        json.dump({"seed": seed, "baseline": BASELINE, "pairs": pairs}, handle, indent=2)
    with open(os.path.join(pairs_dir, "README.md"), "w", encoding="utf-8") as handle:
        handle.write(
            "# Blind A/B listening\n\n"
            f"{len(pairs)} pairs. Each pair is the same sentence from two engines, in random order, "
            "trimmed and leveled to the same loudness.\n\n"
            "1. Play `pair_NN_A.wav`, then `pair_NN_B.wav`.\n"
            "2. In `picks.csv`, write `A` or `B` in the `pick` column for the more natural one "
            "(`=` if you truly can't choose; a tie counts as not preferred).\n"
            "3. Don't open `../ab_key/` until you're done.\n"
            "4. Score: `python scripts/tts_benchmark.py --score-pairs <this directory>`\n"
        )
    print(f"wrote {len(pairs)} blind pairs to {pairs_dir} (key in {key_dir})")
    return pairs_dir


def read_picks(path: str) -> Dict[str, str]:
    with open(path, newline="", encoding="utf-8") as handle:
        return {row["pair"].strip(): (row.get("pick") or "").strip() for row in csv.DictReader(handle) if row.get("pair")}


def score_pairs_dir(pairs_dir: str, key_path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    pairs_dir = os.path.abspath(pairs_dir)
    run_dir = os.path.dirname(pairs_dir)
    key_path = key_path or os.path.join(run_dir, "ab_key", "answer_key.json")
    with open(key_path, encoding="utf-8") as handle:
        key = json.load(handle)["pairs"]
    scores = score_picks(read_picks(os.path.join(pairs_dir, "picks.csv")), key)
    for candidate, s in scores.items():
        rate = "—" if s["preference"] is None else f"{100 * s['preference']:.0f}%"
        print(f"{candidate:<13} preferred {s['preferred']}, edge {s['baseline']}, ties {s['ties']}, "
              f"unanswered {s['unanswered']} -> {rate} {_STATUS_MARK[s['status']]}")
    with open(os.path.join(os.path.dirname(key_path), "scores.json"), "w", encoding="utf-8") as handle:
        json.dump(scores, handle, indent=2)
    results_path = os.path.join(run_dir, "results.json")
    if os.path.isfile(results_path):
        with open(results_path, encoding="utf-8") as handle:
            results = json.load(handle)
        results["naturalness"] = scores
        write_results(run_dir, results)
        print(f"updated {os.path.join(run_dir, 'summary.md')}")
    return scores


# ---------------------------------------------------------------------------
# Voice audition
# ---------------------------------------------------------------------------

async def run_audition(opts: Options, test_set: Dict[str, Any], out_dir: str,
                       extra_voices: Sequence[str] = ()) -> str:
    audition_dir = os.path.join(out_dir, "audition")
    os.makedirs(audition_dir, exist_ok=True)
    opts.candidates = [c for c in opts.candidates if c in ("chirp-unary", "gemini")] or ["chirp-unary", "gemini"]
    providers, skipped = await open_providers(opts)
    if not providers:
        raise SystemExit("Neither Chirp nor Gemini can run here; see the skip messages above.")
    extras = []
    for name in extra_voices:
        full = name if chirp3_voice(name) else f"en-US-Chirp3-HD-{name}"
        if not chirp3_voice(full):
            raise SystemExit(f"unknown Chirp 3: HD voice: {name}")
        extras.append(full)
    rows = []
    try:
        for target in audition_targets(lines=test_set["audition_lines"]):
            voices = list(dict.fromkeys([target["voice"]] + extras))
            files = []
            for voice in voices:
                for provider in providers:
                    rate = target["rate"] if voice == target["voice"] and target["rate"] else opts.rate
                    synth, exc = await timed_synthesis(provider, [target["line"]], [0.0], voice, rate, opts.timeout)
                    label = f"{target['agent']}__{provider.name}__{chirp3_voice(voice)}"
                    if synth is None:
                        print(f"audition {label}: FAILED {classify_error(exc)}: {exc}")
                        continue
                    filename = f"{label}.wav"
                    with open(os.path.join(audition_dir, filename), "wb") as handle:
                        handle.write(pcm_to_wav(bytes(synth.audio), synth.sample_rate))
                    files.append(filename)
                    print(f"audition {label}: {synth.audio_s:.1f} s")
            rows.append((target, files))
    finally:
        for provider in providers:
            await provider.close()
    lines = [
        "# Voice audition", "",
        "One line per agent in the voice from its agent.json `voice` block (DEFAULT_VOICE when it has none). "
        "Gemini TTS has the same 30 voices, so each line is also rendered there for comparison. "
        "Record your choice in each agent.json.", "",
    ]
    if skipped:
        lines += [f"Skipped: " + "; ".join(f"`{k}` ({v})" for k, v in skipped.items()), ""]
    lines += ["| Agent | Name | Voice | Style | Source | Line | Files | Your pick |", "|---|---|---|---|---|---|---|---|"]
    for target, files in rows:
        key = chirp3_voice(target["voice"])
        gender, style = CHIRP3_HD_VOICES.get(key, ("", ""))
        source = target["source"] + (f" — {target['note']}" if target["note"] else "")
        rate = f" @ {target['rate']}" if target["rate"] else ""
        lines.append(f"| {target['agent']} | {target['display_name']} | {key}{rate} | {style}, {gender} | {source} | "
                     f"{target['line']} | " + "<br>".join(f"`{f}`" for f in files) + " | |")
    with open(os.path.join(audition_dir, "index.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print(f"wrote {os.path.join(audition_dir, 'index.md')}")
    return audition_dir


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _csv_list(value: Optional[str]) -> List[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark edge-tts, Chirp 3: HD (unary and streaming) and Gemini TTS; audition agent voices.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--candidates", default=",".join(CANDIDATES),
                        help=f"comma-separated subset of {', '.join(CANDIDATES)} (default: all)")
    parser.add_argument("--runs", type=int, default=3, help="passes over the utterances (default 3)")
    parser.add_argument("--utterances", help="comma-separated utterance ids to run")
    parser.add_argument("--category", help="comma-separated categories: ack, numbers, names, chunked, long")
    parser.add_argument("--limit", type=int, help="at most N utterances, taken across categories")
    parser.add_argument("--out", help="output directory (default scratch/tts_benchmark/<timestamp>)")
    parser.add_argument("--voice", default=DEFAULT_VOICE, help=f"Chirp 3: HD voice; Gemini uses the same one (default {DEFAULT_VOICE})")
    parser.add_argument("--edge-voice", default=EDGE_VOICE, help=f"edge-tts voice (default {EDGE_VOICE}, main's today)")
    parser.add_argument("--gemini-model", default=GEMINI_TTS_MODEL)
    parser.add_argument("--transcribe-model", default=TRANSCRIBE_MODEL)
    parser.add_argument("--rate", type=float, default=1.0, help="speaking rate for edge and Chirp (main uses 1.2 today)")
    parser.add_argument("--chunk-interval-ms", type=float, default=150.0,
                        help="gap between clause chunks of a streamed reply (default 150)")
    parser.add_argument("--prebuffer-ms", type=float, default=0.0, help="audio buffered before playback starts (default 0)")
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds per synthesis before it counts as a timeout")
    parser.add_argument("--reliability", type=int, default=100, help="requests per candidate for reliability; 0 skips")
    parser.add_argument("--no-pronunciation", action="store_true", help="skip the transcription-based pronunciation proxy")
    parser.add_argument("--chirp-endpoint", help="Cloud TTS endpoint, e.g. us-texttospeech.googleapis.com")
    parser.add_argument("--ab-pairs", action="store_true", help="also write blind A/B pairs against edge-tts")
    parser.add_argument("--ab-from", metavar="RUN_DIR", help="write A/B pairs from an earlier run's audio, then exit")
    parser.add_argument("--pairs-limit", type=int, help="at most N pairs per candidate")
    parser.add_argument("--pairs-seed", type=int, default=DEFAULT_PAIRS_SEED)
    parser.add_argument("--score-pairs", metavar="PAIRS_DIR", help="score filled-in picks.csv, then exit")
    parser.add_argument("--answer-key", help="answer key for --score-pairs (default ../ab_key/answer_key.json)")
    parser.add_argument("--rescore", metavar="RUN_DIR",
                        help="re-score a run's transcripts after editing term_alternates (no API calls), then exit")
    parser.add_argument("--audition", action="store_true", help="render one line per agent in its configured voice, then exit")
    parser.add_argument("--audition-voices", help="extra Chirp 3: HD voices to render every line in, e.g. Kore,Puck")
    return parser


def options_from_args(args: argparse.Namespace) -> Options:
    candidates = _csv_list(args.candidates)
    unknown = set(candidates) - set(CANDIDATES)
    if unknown:
        raise SystemExit(f"unknown candidates: {', '.join(sorted(unknown))}")
    if not chirp3_voice(args.voice):
        raise SystemExit(f"--voice must be a Chirp 3: HD voice like {DEFAULT_VOICE}, got {args.voice!r}")
    # Importing Config loads .env, which is where both credentials are configured.
    from core.util.config import Config
    config = Config()
    return Options(
        candidates=candidates,
        runs=max(1, args.runs),
        voice=args.voice,
        edge_voice=args.edge_voice,
        gemini_model=args.gemini_model,
        transcribe_model=args.transcribe_model,
        rate=args.rate,
        chunk_interval_s=args.chunk_interval_ms / 1000.0,
        prebuffer_s=args.prebuffer_ms / 1000.0,
        timeout=args.timeout,
        reliability=max(0, args.reliability),
        pronunciation=not args.no_pronunciation,
        chirp_endpoint=args.chirp_endpoint,
        credentials_file=(config.get(CREDENTIALS_ENV_VAR) or "").strip() or None,
        gemini_api_key=config.gemini_api_key,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    # A full run takes many minutes; when piped to a log, show progress as it happens.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    args = build_parser().parse_args(argv)
    if args.rescore:
        rescore_run(os.path.abspath(args.rescore), load_test_set())
        return 0
    if args.score_pairs:
        score_pairs_dir(args.score_pairs, args.answer_key)
        return 0
    if args.ab_from:
        try:
            build_pairs_from_run(os.path.abspath(args.ab_from), seed=args.pairs_seed, limit=args.pairs_limit)
        except PairsUnavailable as exc:
            raise SystemExit(str(exc)) from exc
        return 0
    opts = options_from_args(args)
    test_set = load_test_set()
    out_dir = os.path.abspath(args.out or os.path.join(DEFAULT_OUT_ROOT, datetime.now().strftime("%Y%m%d-%H%M%S")))
    if args.audition:
        asyncio.run(run_audition(opts, test_set, out_dir, _csv_list(args.audition_voices)))
        return 0
    utterances = select_utterances(test_set["utterances"], _csv_list(args.utterances),
                                   _csv_list(args.category), args.limit)
    if not utterances:
        raise SystemExit("No utterances selected.")
    print(f"{len(utterances)} utterances x {opts.runs} runs -> {out_dir}")
    results = asyncio.run(run_benchmark(opts, test_set, utterances, out_dir, ab_pairs=args.ab_pairs,
                                        pairs_seed=args.pairs_seed))
    print()
    print(render_summary(results))
    print(f"results: {os.path.join(out_dir, 'results.json')}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _bootstrap import ensure_project_interpreter

    ensure_project_interpreter(probe_module="edge_tts")
    sys.exit(main())
