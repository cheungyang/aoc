"""Chirp 3: HD over Cloud Text-to-Speech's bidirectional stream.

One stream per stretch of speech: a config request, then the text a chunk at a
time as it's sent. Audio starts coming back about 200 ms after the first
chunk, while later text is still going in, and goes straight from the gRPC
thread into the speaker. The client is synchronous gRPC on a thread of its
own: the app runs more than one event loop, and an async channel is bound to
one. A stream that fails before its first audio hands everything it was sent,
and the rest of the stretch, to the fallback.
"""
import asyncio
import queue
import threading
from typing import Callable, List, Optional

from core.config.settings import Config
from core.util.errors import format_error_message
from core.voice.foundation import settings

_client = None
_client_lock = threading.Lock()


def _texttospeech():
    from google.cloud import texttospeech
    return texttospeech


def client():
    """The process's Cloud TTS client: one gRPC channel, shared by every stream."""
    global _client
    with _client_lock:
        if _client is None:
            _client = _texttospeech().TextToSpeechClient(client_options={"api_key": Config().google_tts_api_key})
        return _client


def warm_up() -> None:
    """Opens the channel with a free call (listing voices), so the first stretch
    of speech doesn't pay ~400 ms for the client and the TLS handshake.
    Raises if the key is refused."""
    client().list_voices(language_code="en-US", timeout=15)


class ChirpStream:
    """One stretch of speech, streamed through Chirp into a speaker."""

    def __init__(self, voice, speaker, fallback: Callable[[], object]):
        self.voice = voice
        self.speaker = speaker
        self._make_fallback = fallback
        self._fallback = None  # the stream that took over, if this one failed
        self._loop = asyncio.get_running_loop()
        self._texts: "queue.Queue[Optional[str]]" = queue.Queue()
        self._sent: List[str] = []
        self._finished = False
        self._cancelled = False
        self._call = None
        threading.Thread(target=self._run, name="chirp-tts", daemon=True).start()

    def send(self, text: str) -> None:
        if not text:
            return
        if self._fallback is not None:
            self._fallback.send(text)
            return
        self._sent.append(text)
        self._texts.put(text)

    def finish(self) -> None:
        self._finished = True
        if self._fallback is not None:
            self._fallback.finish()
        self._texts.put(None)

    def cancel(self) -> None:
        self._cancelled = True
        self._texts.put(None)
        if self._fallback is not None:
            self._fallback.cancel()
        call = self._call
        if call is not None:
            try:
                call.cancel()
            except Exception:
                pass

    def _requests(self):
        """The config, then each chunk as it's sent. gRPC drains this on its own thread."""
        tts = _texttospeech()
        yield tts.StreamingSynthesizeRequest(streaming_config=tts.StreamingSynthesizeConfig(
            voice=tts.VoiceSelectionParams(language_code=self.voice.locale, name=self.voice.name),
            streaming_audio_config=tts.StreamingAudioConfig(
                audio_encoding=tts.AudioEncoding.PCM,
                sample_rate_hertz=settings.TTS_SAMPLE_RATE,
                speaking_rate=self.voice.rate,
            ),
        ))
        while True:
            text = self._texts.get()
            if text is None or self._cancelled:
                return
            yield tts.StreamingSynthesizeRequest(input=tts.StreamingSynthesisInput(text=text))

    def _run(self) -> None:
        got_audio = False
        try:
            # Returns once the first audio is back (the client prefetches it).
            self._call = client().streaming_synthesize(self._requests(), timeout=settings.TTS_STREAM_TIMEOUT_S)
            for response in self._call:
                if self._cancelled:
                    break
                if response.audio_content:
                    got_audio = True
                    self.speaker.write(response.audio_content)
        except Exception as e:
            if not self._cancelled:
                print(f"[TTS] Chirp stream failed ({self.voice.name}): {format_error_message(e)}")
                if not got_audio:
                    self._loop.call_soon_threadsafe(self._hand_over)
                    return
        self.speaker.finish()

    def _hand_over(self) -> None:
        """On the loop: the fallback says everything sent so far, and the rest."""
        if self._cancelled:
            self.speaker.finish()
            return
        self._fallback = self._make_fallback()
        for text in self._sent:
            self._fallback.send(text)
        if self._finished:
            self._fallback.finish()
