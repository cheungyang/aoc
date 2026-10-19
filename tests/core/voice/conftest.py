"""Voice tests never reach the network.

The streaming recognizer opens a WebSocket per utterance whenever a Gemini key
is configured, and a developer's .env usually has one. Every test here gets the
local provider by default; tests of the live path pass their own provider and
session factory. As a backstop, opening a real Live API connection fails.

Text-to-speech never reaches the network either; that guard is suite-wide, in
tests/conftest.py (`_no_tts_network`), since tests outside this folder build
the voice stack too.
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
