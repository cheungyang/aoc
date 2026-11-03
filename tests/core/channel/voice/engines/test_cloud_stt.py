"""Batch transcription (core/channel/voice/engines/cloud_stt.py): one Interactions API request per utterance."""
import base64
import json

import httpx
import pytest

from core.channel.voice.engines import cloud_stt
from core.channel.voice.engines.cloud_stt import CloudSTTError, interaction_text, transcribe_wav, transcription_request


def test_the_request_carries_the_audio_and_the_live_sessions_settings():
    body = transcription_request(b"RIFFwav", model="gemini-3.5-transcribe", language_codes=["en-US"],
                                 vocabulary=["Frankie", "day planner"], mode="SMART")
    assert body["model"] == "gemini-3.5-transcribe"
    assert body["input"] == [{"type": "audio", "data": base64.b64encode(b"RIFFwav").decode("ascii"),
                              "mime_type": "audio/wav"}]
    assert body["generation_config"]["transcription_config"] == {
        "language_codes": ["en-US"], "mode": "smart", "custom_vocabulary": ["Frankie", "day planner"]}
    assert body["store"] is False


def test_no_vocabulary_sends_none():
    body = transcription_request(b"RIFF", model="m", language_codes=["en-US"], vocabulary=[], mode="VERBATIM")
    assert body["generation_config"]["transcription_config"] == {"language_codes": ["en-US"], "mode": "verbatim"}


def test_the_transcript_is_the_model_outputs_text():
    interaction = {"steps": [
        {"type": "user_input", "content": [{"type": "text", "text": "ignored"}]},
        {"type": "model_output", "content": [{"type": "text", "text": " Add milk "}, {"type": "text", "text": "to the list."}]},
    ]}
    assert interaction_text(interaction) == "Add milk to the list."
    assert interaction_text({}) == ""


def _serve(monkeypatch, handler):
    """transcribe_wav's client, answering with `handler` instead of the network."""
    real = httpx.AsyncClient
    monkeypatch.setattr(cloud_stt.httpx, "AsyncClient",
                        lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs))


@pytest.mark.asyncio
async def test_the_key_goes_in_a_header_and_the_text_comes_back(monkeypatch):
    seen = {}

    def handler(request):
        seen.update(url=str(request.url), key=request.headers.get("x-goog-api-key"), body=json.loads(request.content))
        return httpx.Response(200, json={"steps": [{"type": "model_output",
                                                    "content": [{"type": "text", "text": "hello"}]}]})

    _serve(monkeypatch, handler)
    text = await transcribe_wav(b"RIFF", api_key="secret", model="gemini-3.5-transcribe", timeout=1.0)
    assert text == "hello"
    assert seen["url"] == cloud_stt.INTERACTIONS_URL, "never the key in the URL"
    assert seen["key"] == "secret"
    assert seen["body"]["model"] == "gemini-3.5-transcribe"


@pytest.mark.asyncio
async def test_an_http_error_raises(monkeypatch):
    _serve(monkeypatch, lambda request: httpx.Response(400, text="bad audio"))
    with pytest.raises(CloudSTTError, match="HTTP 400: bad audio"):
        await transcribe_wav(b"RIFF", api_key="secret", model="m", timeout=1.0)
