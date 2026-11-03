"""Speech-to-text for voice turns: streaming Gemini, and the cloud when it fails.

`SpeechRecognizer` is what VoiceManager talks to. It opens a live session for
each utterance (when the provider is "gemini_live" and a Gemini key is
configured), forwards the VAD's audio and segment boundaries to it, and turns
the result into a `Transcript`. When the live session fails or misses its
deadline, the turn is already late, so the fallbacks are quick ones:

    1. what the live session heard before it failed, its partial transcript;
    2. else the utterance's audio to batch transcription (cloud_stt.py),
       within STT_FALLBACK_TIMEOUT_S;
    3. else no words, marked failed: the turn says it didn't catch that.

There is no local recognizer: Whisper on the NAS took ~8 s, slower than asking
again.

A transcript that is the recognizer reciting its vocabulary instead of what
was said (vocabulary_echo.py) counts as no words, as noise does.
"""
import asyncio
import time
from typing import Callable, Iterable, List, Optional

from core.config.settings import Config
from core.config.models import TRANSCRIBE, TRANSCRIBE_LIVE
from core.runtime.foundation.routing import home_channel
from core.voice.foundation import settings
from core.voice.engines.cloud_stt import transcribe_wav
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
        session_factory: Callable = LiveSTTSession,
        cloud: Optional[Callable] = None,
    ):
        self.provider = provider or settings.STT_PROVIDER
        self.vocabulary = _unique_terms(vocabulary)[: settings.STT_VOCABULARY_LIMIT]
        if api_key is None:
            api_key = Config().gemini_api_key or ""
        self._api_key = api_key
        self._session_factory = session_factory
        self._cloud = cloud or transcribe_wav  # batch transcription (cloud_stt.py)

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
        session = utterance.stream
        if session is None:
            return await self._from_cloud(utterance, settings.STT_TRANSCRIBE_TIMEOUT_S)
        text = await session.result()
        if text is not None:
            return self._heard(utterance, text, "gemini_live")
        error = getattr(session, "error", None) or "no final transcript"
        partial = utterance.text_so_far()
        if partial:
            print(f"[SpeechRecognizer] Live transcription failed for {utterance!r} ({error}); "
                  "using what it heard before.")
            return self._heard(utterance, partial, "gemini_live_partial")
        print(f"[SpeechRecognizer] Live transcription failed for {utterance!r} ({error}); "
              "sending the audio to batch transcription.")
        return await self._from_cloud(utterance, settings.STT_FALLBACK_TIMEOUT_S, live_error=error)

    async def _from_cloud(self, utterance: Utterance, timeout: float, live_error: Optional[str] = None) -> Transcript:
        """The utterance's audio to batch transcription, given `timeout` seconds. When that
        fails too, no words, marked failed with the reasons, so the turn can say so."""
        failed = None
        wav = utterance.wav()
        if not wav:
            return Transcript("", "transcribe")
        if not self._api_key:
            failed = "no Gemini API key for batch transcription"
        else:
            started = time.monotonic()
            try:
                text = await asyncio.wait_for(self._cloud(
                    wav, api_key=self._api_key, model=TRANSCRIBE, language_codes=settings.STT_LANGUAGE_CODES,
                    vocabulary=self.vocabulary, mode=settings.STT_MODE, timeout=timeout), timeout)
            except asyncio.TimeoutError:
                failed = f"batch transcription took over {timeout:.1f} s"
            except Exception as e:  # any failure here ends in the same apology
                failed = f"batch transcription failed: {type(e).__name__}: {e}"
            else:
                transcript = self._heard(utterance, text, "transcribe")
                transcript.ms = round((time.monotonic() - started) * 1000)
                return transcript
        reasons = "; then ".join(reason for reason in (live_error, failed) if reason)
        print(f"[SpeechRecognizer] Couldn't transcribe {utterance!r}: {reasons}")
        return Transcript("", "transcribe", failed=reasons)

    def echo(self, utterance: Utterance, text: str) -> Optional[str]:
        """Why `text` is the recognizer reciting its vocabulary rather than
        what was said (vocabulary_echo.py), or None. The floor asks too, before
        words heard over the agent stop its reply."""
        return vocabulary_echo(text, self.vocabulary, utterance.speech_ms())

    def _heard(self, utterance: Utterance, text: str, provider: str) -> Transcript:
        """`text` as the utterance's transcript; for a vocabulary echo, no words, as for
        noise, with what was heard kept for the logs. Both recognizers get the vocabulary."""
        echo = self.echo(utterance, text)
        if echo is None:
            return Transcript(text, provider)
        print(f"[SpeechRecognizer] 🦜 Dropped a vocabulary echo from utterance #{utterance.id} ({echo}): {text!r}")
        return Transcript("", provider, heard=text, echo=echo)
