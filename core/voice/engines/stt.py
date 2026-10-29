"""Speech-to-text for voice turns: streaming Gemini first, local Whisper as the fallback.

`SpeechRecognizer` is what VoiceManager talks to. It opens a live session for
each utterance (when the provider is "gemini_live" and a Gemini key is
configured), forwards the VAD's audio and segment boundaries to it, and turns
the result into a `Transcript`. If the live session fails or misses its
deadline, the utterance's buffered audio goes to Whisper instead, with greedy
decoding: it is the slow path, so it should at least not be slower than needed.

A live transcript that is the recognizer reciting its vocabulary instead of
what was said (vocabulary_echo.py) counts as no words, as noise does. Whisper
is given no vocabulary, so its fallback has nothing to recite.
"""
import asyncio
from typing import Callable, Iterable, List, Optional

from core.config.settings import Config
from core.config.models import TRANSCRIBE_LIVE
from core.runtime.foundation.routing import home_channel
from core.voice.foundation import settings
from core.voice.engines.live_stt import LiveSTTSession
from core.voice.engines.vocabulary_echo import vocabulary_echo
from core.voice.foundation.utterance import Transcript, Utterance


def agent_vocabulary(loader) -> List[str]:
    """Words to bias recognition toward: the name of every agent you can reach
    by name, and its id as spoken ("day planner"). Names like Frankie or Aki
    are exactly what a generic recognizer gets wrong.

    Only the agents with a home channel, where "switch to …" and a hand-off
    lead (core/runtime/foundation/routing.py `home_channel`). Every term is
    also something the recognizer can recite back over noise (a vocabulary
    echo), so the workers you never say (Graph Worker, Terminator) and the
    concierge's id ("main") stay out.
    """
    terms: List[str] = []
    for agent_id in loader.list_agent_ids():
        if not home_channel(agent_id, loader):
            continue
        config = loader.get_agent_config(agent_id) or {}
        terms += [config.get("name"), str(agent_id).replace("-", " ")]
    return _unique_terms(terms)


def _unique_terms(terms: Iterable) -> List[str]:
    """Each term once, whatever its case, as first written; blanks dropped.
    "Receipt Processor" is both a name and an id: sent twice, it was the term
    recited on its own, from fractions of a second of noise."""
    seen, unique = set(), []
    for term in terms:
        if not isinstance(term, str) or not term.strip():
            continue
        if term.strip().casefold() not in seen:
            seen.add(term.strip().casefold())
            unique.append(term.strip())
    return unique


class SpeechRecognizer:
    def __init__(
        self,
        provider: Optional[str] = None,
        vocabulary: Iterable[str] = (),
        api_key: Optional[str] = None,
        whisper_model: Optional[str] = None,
        session_factory: Callable = LiveSTTSession,
    ):
        self.provider = provider or settings.STT_PROVIDER
        self.vocabulary = _unique_terms(vocabulary)[: settings.STT_VOCABULARY_LIMIT]
        if api_key is None:
            api_key = Config().gemini_api_key or ""
        self._api_key = api_key
        self._whisper_model = whisper_model or settings.WHISPER_MODEL
        self._whisper = None
        self._whisper_loading: Optional[asyncio.Future] = None
        self._session_factory = session_factory

    @property
    def streaming(self) -> bool:
        return self.provider == "gemini_live" and bool(self._api_key)

    # --- the utterance's lifecycle, forwarded from the VAD -------------------

    def open(self, utterance: Utterance, on_text: Optional[Callable[[Utterance], None]] = None) -> None:
        """Starts transcribing as the user starts talking."""
        if not self.streaming:
            return
        session = self._session_factory(
            utterance,
            api_key=self._api_key,
            model=TRANSCRIBE_LIVE,
            language_codes=settings.STT_LANGUAGE_CODES,
            vocabulary=self.vocabulary,
            mode=settings.STT_MODE,
            on_text=on_text,
            connect_timeout=settings.STT_CONNECT_TIMEOUT_S,
            final_timeout=settings.STT_FINAL_TIMEOUT_S,
        )
        utterance.stream = session
        session.start()

    def audio(self, utterance: Utterance, pcm: bytes) -> None:
        if utterance.stream is not None:
            utterance.stream.audio(pcm)

    def pause(self, utterance: Utterance) -> None:
        if utterance.stream is not None:
            utterance.stream.pause()

    def resume(self, utterance: Utterance) -> None:
        if utterance.stream is not None:
            utterance.stream.resume()

    def finish(self, utterance: Utterance) -> None:
        if utterance.stream is not None:
            utterance.stream.finish()

    def discard(self, utterance: Utterance) -> None:
        if utterance.stream is not None:
            utterance.stream.cancel()

    # --- the result ----------------------------------------------------------

    async def transcribe(self, utterance: Utterance) -> Transcript:
        """The utterance's final transcript, computed once and cached on it.

        Shielded, because a turn that merges an earlier utterance may be
        cancelled while waiting for it; the next turn picks up the same result.
        """
        if utterance.transcript is not None:
            return utterance.transcript
        if utterance.transcribing is None:
            utterance.transcribing = asyncio.ensure_future(self._transcribe(utterance))
        utterance.transcript = await asyncio.shield(utterance.transcribing)
        return utterance.transcript

    async def _transcribe(self, utterance: Utterance) -> Transcript:
        if utterance.stream is not None:
            text = await utterance.stream.result()
            if text is not None:
                return self._live(utterance, text)
            print(f"[SpeechRecognizer] Live transcription failed for {utterance!r}; falling back to Whisper.")
        text = await self.whisper_transcribe(utterance.wav())
        return Transcript(text, "whisper_fallback" if self.streaming else "whisper")

    def echo(self, utterance: Utterance, text: str) -> Optional[str]:
        """Why live `text` is the recognizer reciting its vocabulary rather than
        what was said (vocabulary_echo.py), or None. The floor asks too, before
        words heard over the agent stop its reply."""
        return vocabulary_echo(text, self.vocabulary, utterance.speech_ms())

    def _live(self, utterance: Utterance, text: str) -> Transcript:
        """The live transcript; for a vocabulary echo, no words, as for noise,
        with what was heard kept for the logs."""
        echo = self.echo(utterance, text)
        if echo is None:
            return Transcript(text, "gemini_live")
        print(f"[SpeechRecognizer] 🦜 Dropped a vocabulary echo from utterance #{utterance.id} ({echo}): {text!r}")
        return Transcript("", "gemini_live", heard=text, echo=echo)

    async def whisper_transcribe(self, wav: bytes) -> str:
        """Local Whisper, loaded on first use: with streaming on, it's only the fallback."""
        if not wav:
            return ""
        if self._whisper is None:
            if self._whisper_loading is None:
                from core.voice.engines.stt_engine import STTEngine

                loop = asyncio.get_running_loop()
                self._whisper_loading = loop.run_in_executor(None, lambda: STTEngine(model_size=self._whisper_model))
            self._whisper = await self._whisper_loading
        return await self._whisper.transcribe(wav)
