"""SpeechRecognizer: streams to a live session per utterance; when it fails, uses
what it heard, else batch transcription, else gives up and says so."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.config.models import TRANSCRIBE, TRANSCRIBE_LIVE
from core.voice.foundation import settings
from core.voice.engines.stt import SpeechRecognizer
from core.voice.foundation.utterance import Utterance


class FakeSession:
    """Records what the recognizer forwards; the test decides the result."""

    instances = []

    def __init__(self, utterance, **kwargs):
        self.utterance = utterance
        self.kwargs = kwargs
        self.calls = []
        self.error = None
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


def _with_audio(ms=2000):
    """An utterance with `ms` of speech. Two seconds by default: a live
    transcript is checked against the speech it came from (vocabulary_echo.py),
    and a sentence from a fraction of a second is too fast to have been said."""
    utterance = Utterance()
    utterance.pcm += bytes(32 * ms)
    return utterance


def test_streams_only_with_the_live_provider_and_a_key():
    assert _live().streaming
    assert not _live(api_key="").streaming
    assert not SpeechRecognizer(provider="transcribe", api_key="key").streaming


def test_the_provider_defaults_to_settings():
    # conftest sets it to "transcribe" so no test opens a socket by accident.
    assert SpeechRecognizer(api_key="key").provider == settings.STT_PROVIDER == "transcribe"


def test_vocabulary_is_deduplicated_and_capped():
    terms = ["Daisy", "Daisy", "", None] + [f"term{i}" for i in range(200)]
    recognizer = _live(vocabulary=terms)
    assert recognizer.vocabulary[:2] == ["Daisy", "term0"]
    assert len(recognizer.vocabulary) == settings.STT_VOCABULARY_LIMIT


def test_a_term_that_differs_only_in_case_is_sent_once():
    """A name that is also its id ("Receipt Processor") was sent twice, and was
    the term recited alone from noise."""
    recognizer = _live(vocabulary=["Receipt Processor", "receipt processor", " Daisy ", "daisy"])
    assert recognizer.vocabulary == ["Receipt Processor", "Daisy"]


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
    recognizer = SpeechRecognizer(provider="transcribe", api_key="key", session_factory=FakeSession)
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
    cloud = AsyncMock()
    recognizer = _live(cloud=cloud)
    utterance = _with_audio()
    recognizer.open(utterance)
    utterance.stream.outcome.set_result("Add milk to the list.")

    transcript = await recognizer.transcribe(utterance)
    assert (transcript.text, transcript.provider, transcript.failed) == ("Add milk to the list.", "gemini_live", None)
    cloud.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_empty_live_result_is_still_a_live_result():
    cloud = AsyncMock()
    recognizer = _live(cloud=cloud)
    utterance = _with_audio()
    recognizer.open(utterance)
    utterance.stream.outcome.set_result("")
    transcript = await recognizer.transcribe(utterance)
    assert (transcript.text, transcript.failed) == ("", None), "noise, not a failure"
    cloud.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_recognizer_reciting_its_vocabulary_is_no_words():
    """What 1.8 s of noise came back as on 2026-10-28, shortened: the turn
    gets no words, as for noise, and what was heard is kept for the logs.
    Not batch transcription either: there was nothing to transcribe."""
    cloud = AsyncMock()
    recognizer = _live(vocabulary=["Aki", "agent designer", "William", "wiki gardener"], cloud=cloud)
    utterance = _with_audio(1836)
    recognizer.open(utterance)
    recital = "Here is the list of added phrases:\n\n- Aki, agent designer\n- William, wiki gardener"
    utterance.stream.outcome.set_result(recital)

    transcript = await recognizer.transcribe(utterance)
    assert (transcript.text, transcript.provider, transcript.heard) == ("", "gemini_live", recital)
    assert transcript.echo == "the recognizer's preamble to its list"
    cloud.assert_not_awaited()


def test_an_echo_is_judged_against_the_speech_it_came_from():
    recognizer = _live(vocabulary=["Receipt Processor"])
    assert recognizer.echo(_with_audio(180), "Receipt Processor.")  # from 180 ms of sound
    assert recognizer.echo(_with_audio(1500), "Receipt Processor.") is None
    assert recognizer.echo(_with_audio(), "Add milk to the list.") is None


def test_the_vocabulary_is_the_agents_you_can_reach_by_name():
    """Pinned against the live agent configs. A worker you never say, or the
    concierge's id, is only one more thing to recite back over noise."""
    from core.loaders.agents_loader import AgentsLoader
    from core.voice.engines.stt import agent_vocabulary

    vocabulary = agent_vocabulary(AgentsLoader())
    for term in ("Frankie", "fitness coach", "William", "wiki gardener", "Aki", "Receipt Processor"):
        assert term in vocabulary, term
    for term in ("Graph Worker", "graph worker low", "Terminator", "script executor", "main"):
        assert term not in vocabulary, term
    assert len({term.casefold() for term in vocabulary}) == len(vocabulary), "each term once, whatever its case"


@pytest.mark.asyncio
async def test_a_failed_live_session_uses_what_it_heard_before_it_failed():
    """The live final missed its deadline, but the session had heard the words: no second request."""
    cloud = AsyncMock()
    recognizer = _live(cloud=cloud)
    utterance = _with_audio()
    recognizer.open(utterance)
    utterance.segments, utterance.interim = ["Add milk"], "to the list"
    utterance.stream.error = "no final transcript 2.0s after the turn ended"
    utterance.stream.outcome.set_result(None)

    transcript = await recognizer.transcribe(utterance)
    assert (transcript.text, transcript.provider, transcript.failed) == (
        "Add milk to the list", "gemini_live_partial", None)
    cloud.assert_not_awaited()


@pytest.mark.asyncio
async def test_with_nothing_heard_live_the_audio_goes_to_batch_transcription():
    cloud = AsyncMock(return_value="Add milk to the list.")
    recognizer = _live(vocabulary=["Frankie"], cloud=cloud)
    utterance = _with_audio()
    recognizer.open(utterance)
    utterance.stream.error = "ConnectionClosedError: no close frame received"
    utterance.stream.outcome.set_result(None)

    transcript = await recognizer.transcribe(utterance)
    assert (transcript.text, transcript.provider, transcript.failed) == ("Add milk to the list.", "transcribe", None)
    assert isinstance(transcript.ms, int)
    wav, kwargs = cloud.call_args.args[0], cloud.call_args.kwargs
    assert wav[:4] == b"RIFF"
    assert (kwargs["model"], kwargs["vocabulary"], kwargs["mode"]) == (TRANSCRIBE, ["Frankie"], settings.STT_MODE)
    assert kwargs["timeout"] == settings.STT_FALLBACK_TIMEOUT_S


@pytest.mark.asyncio
async def test_batch_transcription_past_its_deadline_gives_up_with_both_reasons(monkeypatch):
    monkeypatch.setattr(settings, "STT_FALLBACK_TIMEOUT_S", 0.05)

    async def slow(*_args, **_kwargs):
        await asyncio.sleep(5)

    recognizer = _live(cloud=slow)
    utterance = _with_audio()
    recognizer.open(utterance)
    utterance.stream.error = "no final transcript 2.0s after the turn ended"
    utterance.stream.outcome.set_result(None)

    transcript = await recognizer.transcribe(utterance)
    assert transcript.text == ""
    assert transcript.failed == ("no final transcript 2.0s after the turn ended; "
                                 "then batch transcription took over 0.1 s")


@pytest.mark.asyncio
async def test_a_batch_error_gives_up_too():
    recognizer = _live(cloud=AsyncMock(side_effect=RuntimeError("HTTP 503: unavailable")))
    utterance = _with_audio()
    recognizer.open(utterance)
    utterance.stream.outcome.set_result(None)

    transcript = await recognizer.transcribe(utterance)
    assert transcript.text == ""
    assert transcript.failed == ("no final transcript; "
                                 "then batch transcription failed: RuntimeError: HTTP 503: unavailable")


@pytest.mark.asyncio
async def test_the_batch_provider_transcribes_after_the_turn():
    cloud = AsyncMock(return_value="hello")
    recognizer = SpeechRecognizer(provider="transcribe", api_key="key", cloud=cloud)
    transcript = await recognizer.transcribe(_with_audio())
    assert (transcript.text, transcript.provider) == ("hello", "transcribe")
    assert cloud.call_args.kwargs["timeout"] == settings.STT_TRANSCRIBE_TIMEOUT_S


@pytest.mark.asyncio
async def test_without_a_key_nothing_is_sent_and_the_words_are_not_caught():
    cloud = AsyncMock()
    recognizer = SpeechRecognizer(provider="transcribe", api_key="", cloud=cloud)
    transcript = await recognizer.transcribe(_with_audio())
    assert (transcript.text, transcript.failed) == ("", "no Gemini API key for batch transcription")
    cloud.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_audio_is_no_words_not_a_failure():
    cloud = AsyncMock()
    transcript = await SpeechRecognizer(provider="transcribe", api_key="key", cloud=cloud).transcribe(Utterance())
    assert (transcript.text, transcript.failed) == ("", None)
    cloud.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_transcript_is_computed_once_per_utterance():
    cloud = AsyncMock(return_value="once")
    recognizer = SpeechRecognizer(provider="transcribe", api_key="key", cloud=cloud)
    utterance = _with_audio()
    first, second = await asyncio.gather(recognizer.transcribe(utterance), recognizer.transcribe(utterance))
    third = await recognizer.transcribe(utterance)
    assert first.text == second.text == third.text == "once"
    cloud.assert_awaited_once()


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
