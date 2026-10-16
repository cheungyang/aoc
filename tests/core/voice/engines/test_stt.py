"""SpeechRecognizer: streams to a live session per utterance, falls back to Whisper."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.util.models import TRANSCRIBE_LIVE
from core.voice import settings
from core.voice.engines.stt import SpeechRecognizer
from core.voice.listening.utterance import Utterance


class FakeSession:
    """Records what the recognizer forwards; the test decides the result."""

    instances = []

    def __init__(self, utterance, **kwargs):
        self.utterance = utterance
        self.kwargs = kwargs
        self.calls = []
        self.outcome = asyncio.get_running_loop().create_future()
        FakeSession.instances.append(self)

    def start(self):
        self.calls.append("start")

    def audio(self, pcm):
        self.calls.append(("audio", len(pcm)))

    def pause(self):
        self.calls.append("pause")

    def resume(self):
        self.calls.append("resume")

    def finish(self):
        self.calls.append("finish")

    def cancel(self):
        self.calls.append("cancel")

    async def result(self):
        return await self.outcome


@pytest.fixture(autouse=True)
def _reset_sessions():
    FakeSession.instances = []


def _live(**kwargs):
    kwargs.setdefault("api_key", "key")
    return SpeechRecognizer(provider="gemini_live", session_factory=FakeSession, **kwargs)


def _with_audio(ms=300):
    utterance = Utterance()
    utterance.pcm += bytes(32 * ms)
    return utterance


def test_streams_only_with_the_live_provider_and_a_key():
    assert _live().streaming
    assert not _live(api_key="").streaming
    assert not SpeechRecognizer(provider="whisper", api_key="key").streaming


def test_the_provider_defaults_to_settings():
    # conftest sets it to "whisper" so no test opens a socket by accident.
    assert SpeechRecognizer(api_key="key").provider == settings.STT_PROVIDER == "whisper"


def test_vocabulary_is_deduplicated_and_capped():
    terms = ["Daisy", "Daisy", "", None] + [f"term{i}" for i in range(200)]
    recognizer = _live(vocabulary=terms)
    assert recognizer.vocabulary[:2] == ["Daisy", "term0"]
    assert len(recognizer.vocabulary) == settings.STT_VOCABULARY_LIMIT


@pytest.mark.asyncio
async def test_open_starts_a_live_session_for_the_utterance():
    recognizer = _live(vocabulary=["Frankie"])
    utterance = Utterance()
    on_text = MagicMock()
    recognizer.open(utterance, on_text=on_text)

    session = utterance.stream
    assert session is FakeSession.instances[0]
    assert session.calls == ["start"]
    assert session.kwargs["model"] == TRANSCRIBE_LIVE
    assert session.kwargs["vocabulary"] == ["Frankie"]
    assert session.kwargs["mode"] == settings.STT_MODE
    assert session.kwargs["on_text"] is on_text
    assert session.kwargs["final_timeout"] == settings.STT_FINAL_TIMEOUT_S


@pytest.mark.asyncio
async def test_the_vad_lifecycle_is_forwarded_to_the_session():
    recognizer = _live()
    utterance = Utterance()
    recognizer.open(utterance)
    recognizer.audio(utterance, b"\x00" * 64)
    recognizer.pause(utterance)
    recognizer.resume(utterance)
    recognizer.finish(utterance)
    recognizer.discard(utterance)
    assert utterance.stream.calls == ["start", ("audio", 64), "pause", "resume", "finish", "cancel"]


@pytest.mark.asyncio
async def test_without_streaming_the_lifecycle_is_a_no_op():
    recognizer = SpeechRecognizer(provider="whisper", api_key="key", session_factory=FakeSession)
    utterance = Utterance()
    recognizer.open(utterance)
    recognizer.audio(utterance, b"\x00")
    recognizer.pause(utterance)
    recognizer.finish(utterance)
    recognizer.discard(utterance)
    assert utterance.stream is None
    assert FakeSession.instances == []


@pytest.mark.asyncio
async def test_a_live_result_is_the_transcript():
    recognizer = _live()
    recognizer.whisper_transcribe = AsyncMock()
    utterance = _with_audio()
    recognizer.open(utterance)
    utterance.stream.outcome.set_result("Add milk to the list.")

    transcript = await recognizer.transcribe(utterance)
    assert (transcript.text, transcript.provider) == ("Add milk to the list.", "gemini_live")
    recognizer.whisper_transcribe.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_empty_live_result_is_still_a_live_result():
    recognizer = _live()
    recognizer.whisper_transcribe = AsyncMock()
    utterance = _with_audio()
    recognizer.open(utterance)
    utterance.stream.outcome.set_result("")
    assert (await recognizer.transcribe(utterance)).text == ""
    recognizer.whisper_transcribe.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_live_session_falls_back_to_whisper_on_the_buffered_audio():
    recognizer = _live()
    recognizer.whisper_transcribe = AsyncMock(return_value="add milk to the list")
    utterance = _with_audio()
    recognizer.open(utterance)
    utterance.stream.outcome.set_result(None)

    transcript = await recognizer.transcribe(utterance)
    assert (transcript.text, transcript.provider) == ("add milk to the list", "whisper_fallback")
    wav = recognizer.whisper_transcribe.call_args.args[0]
    assert wav[:4] == b"RIFF"


@pytest.mark.asyncio
async def test_the_local_provider_uses_whisper_directly():
    recognizer = SpeechRecognizer(provider="whisper", api_key="key")
    recognizer.whisper_transcribe = AsyncMock(return_value="hello")
    transcript = await recognizer.transcribe(_with_audio())
    assert (transcript.text, transcript.provider) == ("hello", "whisper")


@pytest.mark.asyncio
async def test_the_transcript_is_computed_once_per_utterance():
    recognizer = SpeechRecognizer(provider="whisper", api_key="key")
    recognizer.whisper_transcribe = AsyncMock(return_value="once")
    utterance = _with_audio()
    first, second = await asyncio.gather(recognizer.transcribe(utterance), recognizer.transcribe(utterance))
    third = await recognizer.transcribe(utterance)
    assert first.text == second.text == third.text == "once"
    recognizer.whisper_transcribe.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_cancelled_turn_does_not_cancel_the_transcription():
    """A merged turn can be cancelled while it waits; the next turn reuses the work."""
    recognizer = _live()
    utterance = _with_audio()
    recognizer.open(utterance)
    waiting = asyncio.create_task(recognizer.transcribe(utterance))
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting

    utterance.stream.outcome.set_result("Still transcribed.")
    assert (await recognizer.transcribe(utterance)).text == "Still transcribed."


@pytest.mark.asyncio
async def test_whisper_loads_lazily_once_and_skips_empty_audio():
    engine = MagicMock()
    engine.transcribe = AsyncMock(return_value="text")
    recognizer = SpeechRecognizer(provider="whisper", api_key="", whisper_model="tiny.en")
    with patch("core.voice.engines.stt_engine.STTEngine", return_value=engine) as engine_class:
        assert await recognizer.whisper_transcribe(b"") == ""
        engine_class.assert_not_called()
        assert await recognizer.whisper_transcribe(b"RIFF1") == "text"
        assert await recognizer.whisper_transcribe(b"RIFF2") == "text"
    engine_class.assert_called_once_with(model_size="tiny.en")
