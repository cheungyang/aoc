"""Streaming speech-to-text: gemini-3.5-transcribe-live over the Live API WebSocket.

One session per utterance, opened at speech onset so the connection and setup
(~0.3-0.5 s) overlap the user still talking; audio heard before setup completes
waits in a queue and is sent as soon as it can be.

Activity detection is manual. Our VAD already knows where speech starts and
stops, and an explicit `activityEnd` makes the server finalize at once (~0.4 s)
rather than waiting out its own silence timer. The VAD also ends the open
segment speculatively a moment into every pause (`pause()`), so by the time the
end-of-turn silence has elapsed the transcript is usually already final. If the
user goes on talking, `resume()` opens a new segment, and the finals are joined.

The google-genai SDK in this image predates the transcription fields this needs
(`customVocabulary`, `mode`, `interimInputTranscription`), so this speaks the
documented WebSocket protocol directly:
https://ai.google.dev/gemini-api/docs/live-api/live-transcribe
"""
import asyncio
import base64
import json
import time
from collections import deque
from typing import Any, Callable, Iterable, Optional

LIVE_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)
MIME_TYPE = "audio/pcm;rate=16000"
# 100 ms of 16 kHz 16-bit mono: the chunk size the docs ask for.
SEND_CHUNK_BYTES = 3200
# The tail of a pause sent ahead of resumed speech, so a soft first syllable
# isn't clipped by the moment it took the VAD to hear it.
RESUME_PREROLL_BYTES = 3200

ACTIVITY_START = json.dumps({"realtimeInput": {"activityStart": {}}})
ACTIVITY_END = json.dumps({"realtimeInput": {"activityEnd": {}}})


class LiveSTTError(Exception):
    """The live session could not be set up, or broke; the caller falls back."""


def setup_message(model: str, language_codes: Iterable[str], vocabulary: Iterable[str], mode: str) -> dict:
    transcription = {"languageCodes": list(language_codes), "mode": mode}
    vocabulary = list(vocabulary)
    if vocabulary:
        transcription["customVocabulary"] = vocabulary
    return {"setup": {
        "model": model if model.startswith("models/") else f"models/{model}",
        "generationConfig": {"responseModalities": ["TEXT"]},
        "inputAudioTranscription": transcription,
        # Manual activity detection: our VAD marks the segments (see module docs).
        "realtimeInputConfig": {"automaticActivityDetection": {"disabled": True}},
    }}


def audio_message(pcm: bytes) -> str:
    return json.dumps({"realtimeInput": {"audio": {
        "data": base64.b64encode(pcm).decode("ascii"),
        "mimeType": MIME_TYPE,
    }}})


def decode(raw: Any) -> dict:
    """Server frames are JSON, sent as text or as UTF-8 bytes."""
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    return json.loads(raw)


def strip_settled(interim: str, settled: Iterable[str]) -> str:
    """An interim for a later activity repeats the earlier finals without their
    punctuation or spacing ("AkiDesign a new" after "Aki."). Drops that repeat,
    comparing letters and digits only; an interim that doesn't start with it
    is returned unchanged."""
    prefix = [c.lower() for c in "".join(settled) if c.isalnum()]
    if not prefix:
        return interim
    matched = 0
    for index, char in enumerate(interim):
        if not char.isalnum():
            continue
        if char.lower() != prefix[matched]:
            return interim
        matched += 1
        if matched == len(prefix):
            return interim[index + 1:].lstrip(" \t,.;:!?-")
    return interim


async def connect_live(url: str, api_key: str):
    """Opens the WebSocket. The key goes in a header, never the URL, so it can't
    end up in a log line or an exception message."""
    from websockets.asyncio.client import connect

    return await connect(url, additional_headers={"x-goog-api-key": api_key}, max_size=None)


class LiveSTTSession:
    """Transcribes one utterance. Every method but `result` is non-blocking and
    must be called on the event loop (the VAD sink posts them there in order)."""

    def __init__(
        self,
        utterance,
        *,
        api_key: str,
        model: str,
        language_codes: Iterable[str] = ("en-US",),
        vocabulary: Iterable[str] = (),
        mode: str = "SMART",
        on_text: Optional[Callable[[Any], None]] = None,
        connect: Optional[Callable] = None,
        connect_timeout: float = 5.0,
        final_timeout: float = 2.0,
    ):
        self.utterance = utterance
        self._api_key = api_key
        self._setup = setup_message(model, language_codes, vocabulary, mode)
        self._on_text = on_text
        self._connect = connect or connect_live
        self._connect_timeout = connect_timeout
        self._final_timeout = final_timeout

        loop = asyncio.get_running_loop()
        self._commands: asyncio.Queue = asyncio.Queue()
        self._result: asyncio.Future = loop.create_future()
        # Segments ended with activityEnd whose ACTIVITY_END hasn't come back yet.
        self._unsettled = 0
        self._settled = asyncio.Event()
        self._settled.set()
        self._task: Optional[asyncio.Task] = None
        self._deadline: Optional[asyncio.TimerHandle] = None
        self._started_at = time.monotonic()
        self._finished_at: Optional[float] = None
        self.error: Optional[str] = None
        self.timings: dict = {}

    # --- controls ------------------------------------------------------------

    def start(self) -> None:
        self._started_at = time.monotonic()
        self._task = asyncio.create_task(self._run(), name=f"live-stt-{self.utterance.id}")

    def audio(self, pcm: bytes) -> None:
        self._commands.put_nowait(("audio", bytes(pcm)))

    def pause(self) -> None:
        """End the open segment now; the user may still go on talking."""
        self._commands.put_nowait(("pause", None))

    def resume(self) -> None:
        self._commands.put_nowait(("resume", None))

    def finish(self) -> None:
        """The turn is over. The transcript is due within `final_timeout`."""
        self._finished_at = time.monotonic()
        self._commands.put_nowait(("finish", None))
        self._deadline = asyncio.get_running_loop().call_later(self._final_timeout, self._on_deadline)

    def cancel(self) -> None:
        """Drop the utterance (too short to be speech)."""
        self._resolve(None)
        if self._task and not self._task.done():
            self._task.cancel()

    async def result(self) -> Optional[str]:
        """The final transcript, or None if the session failed (fall back)."""
        return await asyncio.shield(self._result)

    # --- session -------------------------------------------------------------

    async def _run(self) -> None:
        ws = None
        reader = None
        try:
            ws = await asyncio.wait_for(self._connect(LIVE_URL, self._api_key), self._connect_timeout)
            self.timings["connect_ms"] = self._ms_since(self._started_at)
            await ws.send(json.dumps(self._setup))
            reply = decode(await asyncio.wait_for(ws.recv(), self._connect_timeout))
            if "setupComplete" not in reply:
                raise LiveSTTError(f"unexpected setup reply: {json.dumps(reply)[:200]}")
            self.timings["setup_ms"] = self._ms_since(self._started_at)

            reader = asyncio.create_task(self._read(ws))
            await self._send(ws, reader)
            await self._wait_settled(reader)
            self._resolve(self.utterance.text_so_far())
        except asyncio.CancelledError:
            self._resolve(None)
            raise
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self._resolve(None)
        finally:
            if reader is not None:
                reader.cancel()
            if ws is not None:
                try:
                    await ws.close()
                except Exception:
                    pass

    async def _send(self, ws, reader: asyncio.Task) -> None:
        """Forwards audio and segment boundaries until the turn is finished.

        The server ignores an activityStart sent before it has settled the
        previous activity (its ACTIVITY_END, ~0.4 s after our activityEnd),
        and with it that whole segment. So a segment that resumes sooner is
        held, its audio buffered, and sent as fast as the socket takes it once
        the previous one settles (the reader posts "settled").
        """
        speaking = True        # the VAD's current segment is open
        started = False        # an activity is open on the wire
        ended = False          # a segment ended here but couldn't be sent yet
        finishing = False
        pending = bytearray()  # the current segment's audio, not yet sent
        tail: deque = deque()  # audio heard during a pause: the next segment's pre-roll
        tail_bytes = 0
        while True:
            # Start, stream and end activities as far as the server allows.
            if ended and not started and not pending:
                ended = False  # nothing was said in it
            if not started and (speaking or ended) and self._unsettled == 0:
                await ws.send(ACTIVITY_START)
                started = True
            if started and pending and (ended or len(pending) >= SEND_CHUNK_BYTES):
                await ws.send(audio_message(bytes(pending)))
                pending.clear()
            if started and ended:
                # Counted before sending: the reader can't run until we await.
                self._unsettled += 1
                self._settled.clear()
                await ws.send(ACTIVITY_END)
                started = ended = False
            if finishing and not (started or ended or speaking):
                return

            kind, payload = await self._commands.get()
            if reader.done():
                raise LiveSTTError("connection closed while the user was speaking")
            if kind == "audio":
                if speaking:
                    pending += payload
                else:
                    tail.append(payload)
                    tail_bytes += len(payload)
                    while tail and tail_bytes - len(tail[0]) >= RESUME_PREROLL_BYTES:
                        tail_bytes -= len(tail.popleft())
            elif kind == "resume":
                if not speaking:
                    speaking = True
                    ended = False  # a held segment simply carries on
                    pending += b"".join(tail)
                    tail.clear()
                    tail_bytes = 0
            elif kind in ("pause", "finish"):
                if speaking:
                    speaking = False
                    ended = True
                finishing = finishing or kind == "finish"
            # "settled" needs nothing but another pass of the pump above.

    async def _read(self, ws) -> None:
        async for raw in ws:
            message = decode(raw)
            content = message.get("serverContent") or {}
            changed = False
            interim = content.get("interimInputTranscription")
            if interim is not None:
                text = (interim.get("text") or "").strip()
                self.utterance.interim = strip_settled(text, self.utterance.segments)
                self.timings.setdefault("first_interim_ms", self._ms_since(self._started_at))
                changed = True
            final = content.get("inputTranscription")
            if final is not None:
                text = (final.get("text") or "").strip()
                if text:
                    self.utterance.segments.append(text)
                self.utterance.interim = ""
                changed = True
            if (message.get("voiceActivity") or {}).get("type") == "ACTIVITY_END":
                self._unsettled = max(0, self._unsettled - 1)
                if self._unsettled == 0:
                    self._settled.set()
                    self._commands.put_nowait(("settled", None))  # a held segment may go now
            if changed and self._on_text is not None:
                try:
                    self._on_text(self.utterance)
                except Exception as exc:
                    print(f"[LiveSTT] on_text callback failed: {exc}")
        # Closed: wake the sender, which may be holding a segment for the server.
        self._commands.put_nowait(("closed", None))

    async def _wait_settled(self, reader: asyncio.Task) -> None:
        """Waits for every segment's final, unless the connection closes first."""
        settled = asyncio.create_task(self._settled.wait())
        try:
            done, _ = await asyncio.wait({settled, reader}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            settled.cancel()
        if not self._settled.is_set():
            raise LiveSTTError("connection closed before the transcript was final")

    def _on_deadline(self) -> None:
        if self._result.done():
            return
        self.error = self.error or f"no final transcript {self._final_timeout:.1f}s after the turn ended"
        self._resolve(None)
        if self._task and not self._task.done():
            self._task.cancel()

    def _resolve(self, text: Optional[str]) -> None:
        if self._result.done():
            return
        if self._deadline is not None:
            self._deadline.cancel()
        if self._finished_at is not None:
            self.timings["final_after_end_ms"] = self._ms_since(self._finished_at)
        self._result.set_result(text)
        detail = ", ".join(f"{k} {v:.0f}" for k, v in self.timings.items())
        if text is None and self.error:
            print(f"[LiveSTT] #{self.utterance.id} failed ({self.error}); {detail}")
        elif text is not None:
            print(f"[LiveSTT] #{self.utterance.id} final ({detail}): {text!r}")

    @staticmethod
    def _ms_since(start: float) -> float:
        return (time.monotonic() - start) * 1000
