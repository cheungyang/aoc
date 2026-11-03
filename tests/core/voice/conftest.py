"""Voice tests never reach the network.

The streaming recognizer opens a WebSocket per utterance whenever a Gemini key
is configured, and a developer's .env usually has one; batch transcription
posts the audio. Every test here gets the batch provider by default, which
opens no socket, and batch transcription refuses to run: tests of either path
pass their own provider, session factory or transcriber. As a backstop,
opening a real Live API connection fails too.

Text-to-speech never reaches the network either; that guard is suite-wide, in
tests/conftest.py (`_no_tts_network`), since tests outside this folder build
the voice stack too.

`mock_bot_runner` is the Discord runner a VoiceManager is built with.
"""
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _no_live_stt(monkeypatch):
    from core.voice.foundation import settings
    from core.voice.engines import live_stt, stt

    async def refuse(*_args, **_kwargs):
        raise RuntimeError("tests must not open a Live API connection")

    async def refuse_batch(*_args, **_kwargs):
        raise RuntimeError("tests must not send audio to batch transcription")

    monkeypatch.setattr(settings, "STT_PROVIDER", "transcribe")
    monkeypatch.setattr(live_stt, "connect_live", refuse)
    monkeypatch.setattr(stt, "transcribe_wav", refuse_batch)


@pytest.fixture
def mock_bot_runner():
    runner = MagicMock()
    runner.agent_id = "main"
    runner.bot = MagicMock()
    runner.bot.guilds = []
    runner.bot.loop = MagicMock()
    return runner
