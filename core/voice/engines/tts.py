"""Text to speech: which engine speaks, and in whose voice.

Each stretch of speech is one TTS stream. Text goes in as the agent writes it
(`send`), and audio goes straight into a PCMSpeaker as it's made
(speech/pcm_speaker.py). Chirp 3: HD streams it (chirp_tts.py). edge-tts is
the fallback (edge_tts_stream.py): it speaks every stream when Chirp can't run
on this host, and takes over any Chirp stream that fails before its first
audio. Each agent speaks in its own `voice` block, else DEFAULT_VOICE
(settings.py).
"""
import asyncio
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Optional, Protocol

from core.loaders import agents_loader
from core.config.settings import Config
from core.voice.engines import chirp_tts, edge_tts_stream
from core.voice.foundation import settings


class TTSStream(Protocol):
    """One stretch of speech being synthesized into a speaker."""

    def send(self, text: str) -> None:
        """The next chunk, as soon as it's written."""

    def finish(self) -> None:
        """No more text; the audio plays out."""

    def cancel(self) -> None:
        """Stop now (a barge-in). Safe from any thread."""


@dataclass(frozen=True)
class Voice:
    """How an agent sounds: a Chirp 3: HD voice and a speaking rate."""
    name: str
    rate: float

    @property
    def locale(self) -> str:
        return self.name.split("-Chirp3-HD-")[0]

    @property
    def gender(self) -> str:
        key = settings.chirp3_voice(self.name)
        return settings.CHIRP3_HD_VOICES[key][0] if key else "female"


def agent_voice(agent_id: Optional[str]) -> Voice:
    """The voice `agent_id` speaks in: its `voice` block, the rest from settings."""
    config = agents_loader.AgentsLoader().get_agent_config(agent_id) if agent_id else None
    block = (config or {}).get("voice") or {}
    return Voice(
        name=block.get("name") or settings.DEFAULT_VOICE,
        rate=float(block.get("rate") or settings.DEFAULT_SPEAKING_RATE),
    )


class ChirpCheck:
    """Whether Chirp can speak in this process: found out once, off the event loop.

    The gRPC client is imported in a child process first. A wheel built for a
    newer CPU dies with SIGILL instead of raising, and in this process that
    would take every bot down with it.
    """

    def __init__(self, reason: Optional[str] = None, run: bool = True):
        self.reason = reason  # why Chirp can't be used; None if it can
        self._done = threading.Event()
        if run:
            threading.Thread(target=self._run, name="chirp-check", daemon=True).start()
        else:
            self._done.set()

    def _run(self) -> None:
        try:
            self.reason = self._check() or self._warm_up()
        except Exception as e:
            self.reason = f"the check failed ({e})"
        finally:
            self._done.set()
        if self.reason:
            print(f"[TTS] Speaking with edge-tts: Chirp is unavailable ({self.reason}).")

    @staticmethod
    def _check() -> Optional[str]:
        if settings.TTS_PROVIDER != "chirp3_hd":
            return f"AOC_TTS_PROVIDER is {settings.TTS_PROVIDER!r}"
        if not Config().google_tts_api_key:
            return "GOOGLE_TTS_API_KEY is not set"
        probe = subprocess.run(
            [sys.executable, "-c", "import grpc; from google.cloud import texttospeech"],
            capture_output=True, timeout=120,
        )
        if probe.returncode != 0:
            return f"its gRPC client doesn't load on this host (exit {probe.returncode})"
        return None

    @staticmethod
    def _warm_up() -> Optional[str]:
        """Opens Chirp's channel now; a refused key means edge-tts. A network
        hiccup doesn't: each stream still falls back on its own if it must."""
        from google.api_core import exceptions

        try:
            chirp_tts.warm_up()
        except (exceptions.PermissionDenied, exceptions.Unauthenticated, exceptions.InvalidArgument) as e:
            return f"Cloud Text-to-Speech refused the key ({e.message})"
        except Exception as e:
            print(f"[TTS] Couldn't warm up Chirp's channel: {e}")
        return None

    async def result(self) -> Optional[str]:
        if not self._done.is_set():
            await asyncio.to_thread(self._done.wait)
        return self.reason


_check: Optional[ChirpCheck] = None
_check_lock = threading.Lock()


def chirp_check() -> ChirpCheck:
    """The process's one check, started on first use."""
    global _check
    with _check_lock:
        if _check is None:
            _check = ChirpCheck()
        return _check


class TTSEngine:
    """Opens a stream for each stretch of speech: Chirp 3: HD when it can run here."""

    def __init__(self):
        chirp_check()  # start finding out now, so the first turn doesn't wait

    async def open(self, voice: Voice, speaker) -> TTSStream:
        if await chirp_check().result() is None:
            return chirp_tts.ChirpStream(voice, speaker, fallback=lambda: edge_tts_stream.EdgeStream(voice, speaker))
        return edge_tts_stream.EdgeStream(voice, speaker)
