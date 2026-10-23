"""LiveSTTSession against a fake Live API server that follows the documented protocol.

The fake answers setup with setupComplete, optionally sends an interim for each
audio chunk, and some time after each activityEnd sends the segment's final
transcript, generationComplete, and voiceActivity ACTIVITY_END -- the order the
real service was observed to use (scratch/live_stt_probe.py).
"""
import asyncio
import base64
import json
from unittest.mock import AsyncMock, patch

import pytest

from core.voice.engines import live_stt
from core.voice.engines.live_stt import (
    LIVE_URL,
    RESUME_PREROLL_BYTES,
    SEND_CHUNK_BYTES,
    LiveSTTSession,
    audio_message,
    decode,
    setup_message,
)
from core.voice.engines.live_stt import connect_live as real_connect_live  # before conftest patches it
from core.voice.foundation.utterance import Utterance

WINDOW = 1152  # one 36 ms VAD window of 16 kHz 16-bit mono


class FakeLiveSocket:
    def __init__(self, finals=(), final_delay=0.0, setup_reply=None, interim=None,
                 activity_end=True, drop_after_activity_end=False):
        self.sent = []
        self.closed = False
        self._incoming: asyncio.Queue = asyncio.Queue()
        self._finals = None if finals is None else list(finals)  # None: never answers
        self._final_delay = final_delay
        self._setup_reply = setup_reply or {"setupComplete": {}}
        self._interim = interim
        self._activity_end = activity_end
        self._drop = drop_after_activity_end
        self.in_activity = False
        self.settling = False  # between an activityEnd and its ACTIVITY_END
        self.ignored = []

    # --- what the client sees -------------------------------------------------
    async def send(self, raw):
        message = json.loads(raw)
        self.sent.append(message)
        realtime = message.get("realtimeInput", {})
        # Like the real service: an activity started while the previous one is
        # still settling is ignored, with its audio and its activityEnd.
        if "activityStart" in realtime:
            if self.settling:
                self.ignored.append("activityStart")
            else:
                self.in_activity = True
        if "audio" in realtime and self.in_activity and self._interim:
            self._push({"serverContent": {"interimInputTranscription": {"text": self._interim}}})
        if "activityEnd" in realtime and self.in_activity:
            self.in_activity = False
            if self._drop:
                self._incoming.put_nowait(None)
            else:
                self.settling = True
                asyncio.get_running_loop().call_later(self._final_delay, self._finalize)

    async def recv(self):
        return json.dumps(self._setup_reply).encode()

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._incoming.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def close(self):
        self.closed = True
        self._incoming.put_nowait(None)

    # --- server side -----------------------------------------------------------
    def _finalize(self):
        if self._finals is None:
            return
        text = self._finals.pop(0) if self._finals else ""
        self._push({"serverContent": {"inputTranscription": {"text": text}}})
        self._push({"serverContent": {"generationComplete": True}})
        if self._activity_end:
            self._push({"voiceActivity": {"type": "ACTIVITY_END"}})
            self.settling = False

    def _push(self, message):
        self._incoming.put_nowait(json.dumps(message).encode())  # the server sends bytes

    # --- inspection ------------------------------------------------------------
    def kinds(self):
        out = []
        for message in self.sent:
            if "setup" in message:
                out.append("setup")
            else:
                out.extend(message["realtimeInput"].keys())
        return out

    def audio_chunks(self):
        return [base64.b64decode(m["realtimeInput"]["audio"]["data"])
                for m in self.sent if "audio" in m.get("realtimeInput", {})]


def _session(sock, utterance=None, connect=None, **kwargs):
    async def fake_connect(url, api_key):
        assert url == LIVE_URL and api_key == "test-key"
        return sock

    kwargs.setdefault("final_timeout", 1.0)
    return LiveSTTSession(
        utterance or Utterance(), api_key="test-key", model="gemini-3.5-transcribe-live",
        connect=connect or fake_connect, **kwargs,
    )


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


# --- messages -------------------------------------------------------------------

def test_setup_message_asks_for_manual_activity_detection_and_text():
    message = setup_message("gemini-3.5-transcribe-live", ["en-US"], ["Daisy", "Frankie"], "SMART")
    setup = message["setup"]
    assert setup["model"] == "models/gemini-3.5-transcribe-live"
    assert setup["generationConfig"] == {"responseModalities": ["TEXT"]}
    assert setup["inputAudioTranscription"] == {
        "languageCodes": ["en-US"], "mode": "SMART", "customVocabulary": ["Daisy", "Frankie"],
    }
    assert setup["realtimeInputConfig"] == {"automaticActivityDetection": {"disabled": True}}


def test_setup_message_omits_an_empty_vocabulary_and_keeps_a_full_model_path():
    setup = setup_message("models/x", ["en-US"], [], "VERBATIM")["setup"]
    assert "customVocabulary" not in setup["inputAudioTranscription"]
    assert setup["model"] == "models/x"


def test_audio_message_is_base64_pcm_at_16khz():
    message = json.loads(audio_message(b"\x01\x02"))
    assert message["realtimeInput"]["audio"] == {"data": "AQI=", "mimeType": "audio/pcm;rate=16000"}


def test_decode_accepts_bytes_and_text():
    assert decode(b'{"a": 1}') == {"a": 1}
    assert decode('{"a": 1}') == {"a": 1}


@pytest.mark.asyncio
async def test_the_api_key_goes_in_a_header_not_the_url():
    with patch("websockets.asyncio.client.connect", AsyncMock(return_value="ws")) as connect:
        assert await real_connect_live(LIVE_URL, "secret") == "ws"
    url = connect.call_args.args[0]
    assert "secret" not in url
    assert connect.call_args.kwargs["additional_headers"] == {"x-goog-api-key": "secret"}


# --- one utterance ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_one_segment_yields_its_final_transcript():
    sock = FakeLiveSocket(finals=["Add milk to the shopping list."])
    session = _session(sock)
    session.start()
    for _ in range(6):
        session.audio(bytes(WINDOW))
    session.finish()

    assert await session.result() == "Add milk to the shopping list."
    assert sock.kinds() == ["setup", "activityStart", "audio", "audio", "activityEnd"]
    assert sock.closed
    for key in ("connect_ms", "setup_ms", "final_after_end_ms"):
        assert key in session.timings, key


@pytest.mark.asyncio
async def test_audio_goes_out_in_chunks_of_at_least_100ms_and_nothing_is_lost():
    sock = FakeLiveSocket(finals=["ok"])
    session = _session(sock)
    session.start()
    for _ in range(7):
        session.audio(bytes([7]) * WINDOW)
    session.finish()
    await session.result()

    chunks = sock.audio_chunks()
    assert all(len(chunk) >= SEND_CHUNK_BYTES for chunk in chunks[:-1])
    assert sum(len(chunk) for chunk in chunks) == 7 * WINDOW


@pytest.mark.asyncio
async def test_audio_heard_before_setup_completes_is_sent_after_it_in_order():
    sock = FakeLiveSocket(finals=["Hello there."])

    async def slow_connect(url, api_key):
        await asyncio.sleep(0.05)
        return sock

    session = _session(sock, connect=slow_connect)
    session.start()
    for value in range(1, 4):
        session.audio(bytes([value]) * SEND_CHUNK_BYTES)
    session.finish()

    assert await session.result() == "Hello there."
    assert [chunk[0] for chunk in sock.audio_chunks()] == [1, 2, 3]


@pytest.mark.asyncio
async def test_interims_update_the_utterance_and_notify():
    seen = []
    utterance = Utterance()
    sock = FakeLiveSocket(finals=["Add milk."], interim="add mil", final_delay=0.05)
    session = _session(sock, utterance, on_text=lambda u: seen.append(u.text_so_far()))
    session.start()
    session.audio(bytes(SEND_CHUNK_BYTES))
    await asyncio.sleep(0.02)
    assert utterance.interim == "add mil"
    session.finish()
    await session.result()

    assert seen[0] == "add mil"
    assert seen[-1] == "Add milk."
    assert utterance.interim == ""
    assert "first_interim_ms" in session.timings


# --- pauses: the segment is finalized early, then resumed ----------------------------

@pytest.mark.asyncio
async def test_a_pause_finalizes_the_segment_and_resuming_starts_another():
    utterance = Utterance()
    sock = FakeLiveSocket(finals=["Add milk to the list.", "And eggs."])
    session = _session(sock, utterance)
    session.start()
    session.audio(bytes([1]) * SEND_CHUNK_BYTES)
    session.pause()
    await asyncio.sleep(0.02)
    assert utterance.segments == ["Add milk to the list."]  # final before the turn ended

    for _ in range(4):  # silence heard during the pause
        session.audio(bytes([2]) * WINDOW)
    session.resume()
    session.audio(bytes([3]) * SEND_CHUNK_BYTES)
    session.finish()

    assert await session.result() == "Add milk to the list. And eggs."
    kinds = sock.kinds()
    second = kinds.index("activityStart", 2)
    assert kinds[:4] == ["setup", "activityStart", "audio", "activityEnd"]
    assert kinds[second:] == ["activityStart"] + ["audio"] * (len(kinds) - second - 2) + ["activityEnd"]
    resumed = b"".join(sock.audio_chunks()[1:])
    # The end of the pause goes first, so a soft first syllable isn't clipped.
    preroll = resumed[:-SEND_CHUNK_BYTES]
    assert set(preroll) == {2} and RESUME_PREROLL_BYTES <= len(preroll) < RESUME_PREROLL_BYTES + WINDOW
    assert set(resumed[-SEND_CHUNK_BYTES:]) == {3}


@pytest.mark.asyncio
async def test_finishing_during_a_pause_sends_no_second_activity_end():
    sock = FakeLiveSocket(finals=["What's the weather?"])
    session = _session(sock)
    session.start()
    session.audio(bytes(SEND_CHUNK_BYTES))
    session.pause()
    session.audio(bytes(WINDOW))  # silence; never sent
    session.finish()

    assert await session.result() == "What's the weather?"
    assert sock.kinds().count("activityEnd") == 1
    assert len(sock.audio_chunks()) == 1


@pytest.mark.asyncio
async def test_a_segment_with_no_speech_settles_with_empty_text():
    sock = FakeLiveSocket(finals=[""])
    session = _session(sock)
    session.start()
    session.audio(bytes(SEND_CHUNK_BYTES))
    session.finish()
    assert await session.result() == ""


# --- failures: None means "fall back to Whisper" ---------------------------------------

@pytest.mark.asyncio
async def test_a_rejected_setup_fails_over():
    sock = FakeLiveSocket(setup_reply={"error": {"code": 403, "message": "API key not valid"}})
    session = _session(sock)
    session.start()
    session.finish()
    assert await session.result() is None
    assert "unexpected setup reply" in session.error


@pytest.mark.asyncio
async def test_a_failed_connection_fails_over():
    async def broken(url, api_key):
        raise OSError("network unreachable")

    session = _session(None, connect=broken)
    session.start()
    session.finish()
    assert await session.result() is None
    assert "network unreachable" in session.error


@pytest.mark.asyncio
async def test_a_slow_connection_times_out():
    async def hangs(url, api_key):
        await asyncio.sleep(10)

    session = _session(None, connect=hangs, connect_timeout=0.05)
    session.start()
    assert await asyncio.wait_for(session.result(), 1) is None
    assert "TimeoutError" in session.error


@pytest.mark.asyncio
async def test_a_missing_final_misses_the_deadline():
    sock = FakeLiveSocket(finals=None)  # never answers
    session = _session(sock, final_timeout=0.05)
    session.start()
    session.audio(bytes(SEND_CHUNK_BYTES))
    session.finish()
    assert await asyncio.wait_for(session.result(), 1) is None
    assert "no final transcript" in session.error
    await _settle()
    assert sock.closed


@pytest.mark.asyncio
async def test_a_final_without_activity_end_misses_the_deadline():
    # Settling waits for ACTIVITY_END: a final alone may be one of several.
    sock = FakeLiveSocket(finals=["Hello."], activity_end=False)
    session = _session(sock, final_timeout=0.05)
    session.start()
    session.audio(bytes(SEND_CHUNK_BYTES))
    session.finish()
    assert await asyncio.wait_for(session.result(), 1) is None


@pytest.mark.asyncio
async def test_a_connection_closed_before_the_final_fails_over():
    sock = FakeLiveSocket(drop_after_activity_end=True)
    session = _session(sock)
    session.start()
    session.audio(bytes(SEND_CHUNK_BYTES))
    session.finish()
    assert await asyncio.wait_for(session.result(), 1) is None
    assert "closed before the transcript was final" in session.error


@pytest.mark.asyncio
async def test_cancel_drops_the_utterance():
    sock = FakeLiveSocket(finals=["unused"])
    session = _session(sock)
    session.start()
    session.audio(bytes(SEND_CHUNK_BYTES))
    await asyncio.sleep(0.01)
    session.cancel()
    assert await session.result() is None
    await _settle()
    assert sock.closed


@pytest.mark.asyncio
async def test_result_survives_a_cancelled_waiter():
    sock = FakeLiveSocket(finals=["Still here."], final_delay=0.05)
    session = _session(sock)
    session.start()
    session.audio(bytes(SEND_CHUNK_BYTES))
    session.finish()
    waiter = asyncio.create_task(session.result())
    await asyncio.sleep(0.01)
    waiter.cancel()
    assert await session.result() == "Still here."


def test_conftest_refuses_real_connections():
    assert live_stt.connect_live is not real_connect_live


@pytest.mark.asyncio
async def test_a_connection_lost_while_a_segment_is_held_fails_over_at_once():
    sock = FakeLiveSocket(drop_after_activity_end=True)
    session = _session(sock, final_timeout=5.0)
    session.start()
    session.audio(bytes(SEND_CHUNK_BYTES))
    session.pause()
    session.resume()  # held: the server never settles, it hangs up
    session.audio(bytes(SEND_CHUNK_BYTES))
    assert await asyncio.wait_for(session.result(), 1) is None
    assert "connection closed" in session.error


@pytest.mark.asyncio
async def test_a_segment_resumed_before_the_last_one_settles_is_held_until_it_does():
    """Seen live: "Aki, design a new agent..." lost everything after "Aki,"
    because the resumed segment started while the server was still settling."""
    utterance = Utterance()
    sock = FakeLiveSocket(finals=["Aki.", "Design a new agent."], final_delay=0.05)
    session = _session(sock, utterance)
    session.start()
    session.audio(bytes([1]) * SEND_CHUNK_BYTES)
    session.pause()
    session.audio(bytes([2]) * WINDOW)
    session.resume()  # long before the server settles
    session.audio(bytes([3]) * SEND_CHUNK_BYTES)
    await asyncio.sleep(0.01)
    assert sock.kinds().count("activityStart") == 1, "held while the server settles"
    session.audio(bytes([3]) * SEND_CHUNK_BYTES)
    session.finish()

    assert await session.result() == "Aki. Design a new agent."
    assert sock.ignored == []
    assert sock.kinds() == ["setup", "activityStart", "audio", "activityEnd",
                            "activityStart", "audio", "activityEnd"]
    held = sock.audio_chunks()[1]
    assert held[:WINDOW] == bytes([2]) * WINDOW, "the pause's tail still leads the held segment"
    assert len(held) == WINDOW + 2 * SEND_CHUNK_BYTES


@pytest.mark.parametrize("interim, settled, expected", [
    ("AkiDesign a new", ["Aki."], "Design a new"),
    ("Aki, design a new", ["Aki."], "design a new"),
    ("Design a new", ["Aki."], "Design a new"),
    ("add milk", [], "add milk"),
    ("Ak", ["Aki."], "Ak"),
])
def test_strip_settled(interim, settled, expected):
    assert live_stt.strip_settled(interim, settled) == expected


@pytest.mark.asyncio
async def test_an_interim_repeating_earlier_finals_is_trimmed():
    utterance = Utterance()
    sock = FakeLiveSocket(finals=["Aki.", "Design a new agent."])
    session = _session(sock, utterance)
    session.start()
    session.audio(bytes(SEND_CHUNK_BYTES))
    session.pause()
    await asyncio.sleep(0.02)
    sock._interim = "AkiDesign a new"  # what the service sends for the next segment
    session.resume()
    session.audio(bytes(SEND_CHUNK_BYTES))
    await asyncio.sleep(0.02)
    assert utterance.interim == "Design a new"
    assert utterance.text_so_far() == "Aki. Design a new"
    session.finish()
    assert await session.result() == "Aki. Design a new agent."
