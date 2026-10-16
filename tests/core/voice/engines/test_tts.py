"""Text to speech: whose voice, which engine, Chirp's stream, and the edge-tts fallback."""
import asyncio
import queue
import subprocess
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from core.voice import settings
from core.voice.engines import chirp_tts, edge_tts_stream, tts
from core.voice.engines.chirp_tts import ChirpStream
from core.voice.engines.edge_tts_stream import EdgeStream
from core.voice.engines.edge_tts_stream import synthesize as edge_synthesize  # before conftest stubs it
from core.voice.engines.tts import ChirpCheck, TTSEngine, Voice, agent_voice
from core.voice.speech.pcm_speaker import PCMSpeaker

PUCK = Voice("en-US-Chirp3-HD-Puck", 1.2)


async def _until(condition, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting"
        await asyncio.sleep(0.005)


# --- whose voice -------------------------------------------------------------

def test_an_agent_speaks_in_its_own_voice_at_the_shared_rate():
    assert agent_voice("day-planner") == Voice("en-US-Chirp3-HD-Zephyr", settings.DEFAULT_SPEAKING_RATE)


def test_an_agent_without_a_voice_block_speaks_in_the_default_voice():
    assert agent_voice("main") == Voice(settings.DEFAULT_VOICE, settings.DEFAULT_SPEAKING_RATE)
    assert agent_voice(None).name == settings.DEFAULT_VOICE


def test_a_rate_in_the_voice_block_wins():
    with patch("core.loaders.agents_loader.AgentsLoader") as loader:
        loader.return_value.get_agent_config.return_value = {"voice": {"name": "en-US-Chirp3-HD-Puck", "rate": 1.05}}
        assert agent_voice("excursion-planner") == Voice("en-US-Chirp3-HD-Puck", 1.05)


def test_a_voice_knows_its_locale_and_gender():
    assert PUCK.locale == "en-US" and PUCK.gender == "male"
    assert Voice("en-US-Chirp3-HD-Zephyr", 1.0).gender == "female"


# --- which engine ------------------------------------------------------------

class TestChirpCheck:
    def test_no_key_means_edge(self, monkeypatch):
        monkeypatch.setattr(settings, "TTS_PROVIDER", "chirp3_hd")
        with patch("core.voice.engines.tts.Config") as config:
            config.return_value.google_tts_api_key = ""
            assert ChirpCheck._check() == "GOOGLE_TTS_API_KEY is not set"

    def test_a_client_that_crashes_on_import_means_edge_and_not_a_crash(self, monkeypatch):
        """A SIGILL in the child process is an exit code here, not a dead bot."""
        monkeypatch.setattr(settings, "TTS_PROVIDER", "chirp3_hd")
        with patch("core.voice.engines.tts.Config") as config, \
             patch("core.voice.engines.tts.subprocess.run", return_value=MagicMock(returncode=-4)) as run:
            config.return_value.google_tts_api_key = "key"
            assert "doesn't load" in ChirpCheck._check()
        assert run.call_args.args[0][0] == sys.executable

    def test_chirp_when_its_client_loads(self, monkeypatch):
        monkeypatch.setattr(settings, "TTS_PROVIDER", "chirp3_hd")
        with patch("core.voice.engines.tts.Config") as config, \
             patch("core.voice.engines.tts.subprocess.run", return_value=MagicMock(returncode=0)):
            config.return_value.google_tts_api_key = "key"
            assert ChirpCheck._check() is None

    def test_a_refused_key_means_edge_but_a_network_hiccup_does_not(self, monkeypatch):
        from google.api_core import exceptions

        monkeypatch.setattr(chirp_tts, "warm_up", MagicMock(side_effect=exceptions.PermissionDenied("API key not valid")))
        assert "refused the key" in ChirpCheck._warm_up()
        monkeypatch.setattr(chirp_tts, "warm_up", MagicMock(side_effect=ConnectionError("network is unreachable")))
        assert ChirpCheck._warm_up() is None, "each stream still falls back on its own"

    def test_edge_only_when_configured(self, monkeypatch):
        monkeypatch.setattr(settings, "TTS_PROVIDER", "edge")
        assert ChirpCheck._check() == "AOC_TTS_PROVIDER is 'edge'"


@pytest.mark.asyncio
async def test_the_engine_opens_chirp_when_it_can_and_edge_when_it_cannot(monkeypatch):
    monkeypatch.setattr(chirp_tts, "ChirpStream", lambda voice, speaker, fallback: ("chirp", fallback))
    monkeypatch.setattr(edge_tts_stream, "EdgeStream", lambda voice, speaker: "edge")
    speaker = PCMSpeaker(asyncio.get_running_loop())

    monkeypatch.setattr(tts, "_check", ChirpCheck(None, run=False))
    kind, fallback = await TTSEngine().open(PUCK, speaker)
    assert kind == "chirp" and fallback() == "edge"

    monkeypatch.setattr(tts, "_check", ChirpCheck("GOOGLE_TTS_API_KEY is not set", run=False))
    assert await TTSEngine().open(PUCK, speaker) == "edge"


# --- Chirp's stream ----------------------------------------------------------

class FakeChirpClient:
    """streaming_synthesize as gRPC runs it: the requests are drained on another
    thread while responses come back; one response per chunk of text."""

    def __init__(self, fail=None):
        self.requests = []
        self.fail = fail

    def streaming_synthesize(self, requests, timeout=None):
        if self.fail:
            raise self.fail
        responses = queue.Queue()

        def drain():
            for request in requests:
                self.requests.append(request)
                if request.input.text:
                    responses.put(MagicMock(audio_content=bytes(1920)))
            responses.put(None)

        threading.Thread(target=drain, daemon=True).start()
        return _Responses(responses)


class _Responses:
    def __init__(self, items):
        self._items = items

    def __iter__(self):
        return self

    def __next__(self):
        item = self._items.get(timeout=5)
        if item is None:
            raise StopIteration
        return item

    def cancel(self):
        self._items.put(None)


@pytest.mark.asyncio
async def test_chirp_streams_the_config_then_each_chunk_into_the_speaker(monkeypatch):
    fake = FakeChirpClient()
    monkeypatch.setattr(chirp_tts, "client", lambda: fake)
    speaker = PCMSpeaker(asyncio.get_running_loop())
    stream = ChirpStream(PUCK, speaker, fallback=MagicMock())
    stream.send("Checking your calendar, ")
    stream.send("and your tasks for today. ")
    stream.finish()

    assert await asyncio.wait_for(speaker.ready(), 2) is True
    await _until(lambda: len(fake.requests) == 3 and speaker._finished)
    config = fake.requests[0].streaming_config
    assert (config.voice.name, config.voice.language_code) == ("en-US-Chirp3-HD-Puck", "en-US")
    assert config.streaming_audio_config.sample_rate_hertz == settings.TTS_SAMPLE_RATE
    assert config.streaming_audio_config.speaking_rate == pytest.approx(1.2)
    assert [r.input.text for r in fake.requests[1:]] == ["Checking your calendar, ", "and your tasks for today. "]


@pytest.mark.asyncio
async def test_a_chirp_stream_that_fails_before_any_audio_hands_everything_to_the_fallback(monkeypatch):
    monkeypatch.setattr(chirp_tts, "client", lambda: FakeChirpClient(fail=RuntimeError("403 API key not valid")))
    fallback = MagicMock()
    stream = ChirpStream(PUCK, PCMSpeaker(asyncio.get_running_loop()), fallback=lambda: fallback)
    stream.send("First chunk. ")
    await _until(lambda: stream._fallback is not None)
    stream.send("Second chunk. ")
    stream.finish()

    assert [c.args[0] for c in fallback.send.call_args_list] == ["First chunk. ", "Second chunk. "]
    fallback.finish.assert_called_once()


@pytest.mark.asyncio
async def test_a_cancelled_chirp_stream_stops_without_the_fallback(monkeypatch):
    monkeypatch.setattr(chirp_tts, "client", lambda: FakeChirpClient(fail=RuntimeError("cancelled")))
    make_fallback = MagicMock()
    speaker = PCMSpeaker(asyncio.get_running_loop())
    stream = ChirpStream(PUCK, speaker, fallback=make_fallback)
    stream.cancel()
    await _until(lambda: speaker._finished)
    make_fallback.assert_not_called()


# --- the edge-tts fallback ---------------------------------------------------

@pytest.mark.asyncio
async def test_the_fallback_says_each_chunk_in_order(monkeypatch):
    said = []

    async def fake_synthesize(text, voice):
        said.append(text)
        return bytes(1920)

    monkeypatch.setattr(edge_tts_stream, "synthesize", fake_synthesize)
    speaker = PCMSpeaker(asyncio.get_running_loop())
    stream = EdgeStream(PUCK, speaker)
    stream.send("One. ")
    stream.send("Two. ")
    stream.finish()
    await asyncio.wait_for(stream._task, 2)
    assert said == ["One. ", "Two. "]
    assert await speaker.ready() is True


@pytest.mark.asyncio
async def test_the_fallback_keeps_the_voices_gender_and_rate_and_decodes_to_pcm(monkeypatch):
    mp3 = subprocess.run(
        [settings.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=0.5", "-ar", "24000", "-ac", "1", "-f", "mp3", "pipe:1"],
        capture_output=True, check=True,
    ).stdout
    made = {}

    class FakeCommunicate:
        def __init__(self, text, voice, rate):
            made.update(text=text, voice=voice, rate=rate)

        async def stream(self):
            yield {"type": "WordBoundary"}
            yield {"type": "audio", "data": mp3}

    monkeypatch.setitem(sys.modules, "edge_tts", MagicMock(Communicate=FakeCommunicate))
    pcm = await edge_synthesize("Hello.", PUCK)
    assert made == {"text": "Hello.", "voice": settings.EDGE_FALLBACK_VOICES["male"], "rate": "+20%"}
    assert abs(len(pcm) / (2 * settings.TTS_SAMPLE_RATE) - 0.5) < 0.1, "48 kHz mono, as long as the audio"
