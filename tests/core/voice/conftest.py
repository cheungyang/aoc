"""Voice tests never reach the network.

The streaming recognizer opens a WebSocket per utterance whenever a Gemini key
is configured, and a developer's .env usually has one. Every test here gets the
local provider by default; tests of the live path pass their own provider and
session factory. As a backstop, opening a real Live API connection fails.

Text-to-speech never reaches the network either: Chirp counts as unavailable
without running its gRPC check, and a real Chirp or edge-tts request fails.
Tests that speak use `FakeTTS` (tests/helpers.py).
"""
import pytest


@pytest.fixture(autouse=True)
def _no_live_stt(monkeypatch):
    from core.voice import settings
    from core.voice.engines import live_stt

    async def refuse(*_args, **_kwargs):
        raise RuntimeError("tests must not open a Live API connection")

    monkeypatch.setattr(settings, "STT_PROVIDER", "whisper")
    monkeypatch.setattr(live_stt, "connect_live", refuse)


@pytest.fixture(autouse=True)
def _no_tts_network(monkeypatch):
    from core.voice.engines import chirp_tts, edge_tts_stream, tts

    def refuse(*_args, **_kwargs):
        raise RuntimeError("tests must not reach a TTS service")

    async def refuse_async(*_args, **_kwargs):
        refuse()

    monkeypatch.setattr(tts, "_check", tts.ChirpCheck("disabled in tests", run=False))
    monkeypatch.setattr(chirp_tts, "client", refuse)
    monkeypatch.setattr(edge_tts_stream, "synthesize", refuse_async)
