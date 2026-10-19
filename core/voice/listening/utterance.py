"""One stretch of a user's speech, from onset to the end of the turn.

The VAD sink creates an `Utterance` on the audio thread when speech starts and
keeps appending its PCM; the live transcriber fills in its text on the event
loop. The two sides meet only through plain attributes, each written by one
side: the audio thread writes `pcm` and `speech_end_at`; the loop writes
`segments`, `interim` and the rest. The audio thread reads the text through
`text_so_far()` (for end-of-turn timing); the loop reads the PCM through
`wav()` only after the turn has ended (for the Whisper fallback).
"""
import io
import itertools
import time
import wave
from dataclasses import dataclass
from typing import Any, List, Optional

import numpy as np

SAMPLE_RATE = 16000
BYTES_PER_MS = SAMPLE_RATE * 2 // 1000  # 16-bit mono

_ids = itertools.count(1)


@dataclass
class Transcript:
    text: str
    provider: str  # "gemini_live", "whisper", or "whisper_fallback"


class Utterance:
    def __init__(self, user: Any = None, key: Any = None):
        self.id = next(_ids)
        self.user = user
        self.key = key
        self.started_at = time.monotonic()
        # time.monotonic() of the last voiced frame, set when the turn ends.
        self.speech_end_at: Optional[float] = None
        # time.monotonic() when the turn ended (the end-of-turn silence elapsed).
        self.finished_at: Optional[float] = None
        self.pcm = bytearray()  # 16 kHz 16-bit mono, onset through end of turn

        # Filled in on the event loop by the live transcriber.
        self.segments: List[str] = []  # finalized text, one per speech segment
        self.interim = ""              # latest hypothesis for the open segment
        self.stream = None             # its LiveSTTSession, if one was opened
        self.transcript: Optional[Transcript] = None
        self.transcribing = None       # the task computing `transcript`

        # Barge-in state, owned by the Floor (floor.py).
        self.confirmed = False  # its words confirmed an interruption
        self.ducked = False     # it lowered the agent's volume when it began

    def text_so_far(self) -> str:
        parts = list(self.segments)
        if self.interim:
            parts.append(self.interim)
        return " ".join(part.strip() for part in parts if part and part.strip())

    def duration_ms(self) -> float:
        return len(self.pcm) / BYTES_PER_MS

    def wav(self) -> bytes:
        return pcm_to_wav(bytes(self.pcm))

    def __repr__(self) -> str:
        return f"Utterance#{self.id}({self.duration_ms():.0f} ms, {self.text_so_far()!r})"


def pcm_to_wav(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """16-bit mono PCM to a WAV, peak-normalized so quiet speech (boosted up to 8x)
    reaches Whisper at a usable level."""
    if not pcm:
        return b""

    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    if peak > 50.0:
        audio = np.clip(audio * min(28000.0 / peak, 8.0), -32768.0, 32767.0)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(audio.astype(np.int16).tobytes())
    return buffer.getvalue()
