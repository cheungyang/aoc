"""The speaker: a stretch of speech as Discord plays it, while it's still being made.

A TTS stream writes PCM into it as the audio arrives, from its own thread
(`write`); discord.py's player thread takes it out 20 ms at a time (`read`).
No FFmpeg and no temp file sit in between. The audio queue starts playing a
speaker once its first audio is in (`ready`), so speech never opens on
padding. After that, a frame that hasn't arrived yet plays as silence rather
than ending playback: a late chunk is a short gap, not a cut-off.
"""
import array
import asyncio
import threading
import time
from typing import Callable, Optional

import discord

FRAME_BYTES = 3840  # 20 ms of 48 kHz 16-bit stereo: one packet, as discord.py sends it
SILENCE = bytes(FRAME_BYTES)
# A speaker whose stream goes quiet this long without finishing is ended.
STALL_S = 15.0


class PCMSpeaker(discord.AudioSource):
    """48 kHz mono PCM in (`write`), 20 ms stereo frames out (`read`)."""

    def __init__(self, loop: asyncio.AbstractEventLoop, on_audio: Optional[Callable[[], None]] = None):
        self._loop = loop
        self._on_audio = on_audio  # called on the loop when the first audio arrives
        self._lock = threading.Lock()
        self._buffer = bytearray()
        self._odd = b""  # half a sample, when a chunk splits one
        self._finished = False
        self._ready = asyncio.Event()
        self._last_audio = time.monotonic()
        self.received = False
        self.underruns = 0  # silent frames played while waiting for audio
        self.stream = None  # the TTS stream feeding this speaker; stopped with it

    # --- the TTS side: any thread ---------------------------------------------

    def write(self, pcm: bytes) -> None:
        """Appends 16-bit little-endian mono PCM at 48 kHz."""
        data = self._odd + pcm
        cut = len(data) - len(data) % 2
        data, self._odd = data[:cut], data[cut:]
        if not data:
            return
        frames = _stereo(data)
        with self._lock:
            if self._finished:
                return
            self._buffer += frames
            self._last_audio = time.monotonic()
            first, self.received = not self.received, True
        if first:
            self._call_soon(self._on_audio)
            self._call_soon(self._ready.set)

    def finish(self) -> None:
        """No more audio is coming; what's buffered still plays."""
        with self._lock:
            self._finished = True
        self._call_soon(self._ready.set)

    # --- the player side: discord.py's thread ---------------------------------

    def read(self) -> bytes:
        with self._lock:
            if len(self._buffer) >= FRAME_BYTES:
                frame = bytes(self._buffer[:FRAME_BYTES])
                del self._buffer[:FRAME_BYTES]
                return frame
            if not self._finished and time.monotonic() - self._last_audio > STALL_S:
                self._finished = True
            if self._finished:
                frame = bytes(self._buffer).ljust(FRAME_BYTES, b"\0") if self._buffer else b""
                self._buffer.clear()
                return frame
            self.underruns += 1
            return SILENCE

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        """Playback ended, or was cut off by a barge-in: stop making audio for it."""
        with self._lock:
            self._finished = True
            self._buffer.clear()
        self._call_soon(self._ready.set)
        if self.stream is not None:
            self.stream.cancel()

    # --- the audio queue's side: the loop -------------------------------------

    async def ready(self) -> bool:
        """Waits for the first audio, or the end; True if there is audio to play."""
        await self._ready.wait()
        return self.received

    def _call_soon(self, callback: Optional[Callable[[], None]]) -> None:
        if callback is None:
            return
        try:
            self._loop.call_soon_threadsafe(callback)
        except RuntimeError:
            pass  # the loop is closed: nothing is waiting any more


def _stereo(mono: bytes) -> bytes:
    """16-bit mono -> the same samples in both channels (copied, so byte order is kept)."""
    samples = array.array("h", mono)
    out = array.array("h", bytes(len(mono) * 2))
    out[0::2] = samples
    out[1::2] = samples
    return out.tobytes()
