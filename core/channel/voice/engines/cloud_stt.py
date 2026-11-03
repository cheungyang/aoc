"""Batch speech-to-text: one utterance's audio to gemini-3.5-transcribe, after the turn.

The fallback when a live session (live_stt.py) gives no final transcript and
heard nothing usable before it failed, and all of speech-to-text when the
recognizer doesn't stream (STT_PROVIDER="transcribe"). One POST to the
Interactions API with the audio inline, as the documented REST call:
https://ai.google.dev/gemini-api/docs/transcribe

The key goes in a header, never the URL, so it can't end up in a log line or
an exception message.
"""
import base64
from typing import Any, Dict, Iterable

import httpx

INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"


class CloudSTTError(Exception):
    """The transcription request failed; the caller gives up on the utterance."""


def transcription_request(wav: bytes, *, model: str, language_codes: Iterable[str], vocabulary: Iterable[str],
                          mode: str) -> Dict[str, Any]:
    """The request body. `mode` is the live session's ("SMART" or "VERBATIM"), in this API's spelling."""
    config: Dict[str, Any] = {"language_codes": list(language_codes), "mode": mode.lower()}
    vocabulary = list(vocabulary)
    if vocabulary:
        config["custom_vocabulary"] = vocabulary
    return {
        "model": model,
        "input": [{"type": "audio", "data": base64.b64encode(wav).decode("ascii"), "mime_type": "audio/wav"}],
        "generation_config": {"transcription_config": config},
        "store": False,
    }


def interaction_text(interaction: Dict[str, Any]) -> str:
    """The transcript in a finished interaction: its model output's text parts, joined."""
    texts = []
    for step in interaction.get("steps") or []:
        if step.get("type") != "model_output":
            continue
        for content in step.get("content") or []:
            if content.get("type") == "text" and content.get("text"):
                texts.append(content["text"].strip())
    return " ".join(texts).strip()


async def transcribe_wav(wav: bytes, *, api_key: str, model: str, language_codes: Iterable[str] = ("en-US",),
                         vocabulary: Iterable[str] = (), mode: str = "SMART", timeout: float = 10.0) -> str:
    """The transcript of `wav`: "" if nothing was said. Raises CloudSTTError for a failed
    request, or httpx's timeout past `timeout` seconds."""
    body = transcription_request(wav, model=model, language_codes=language_codes, vocabulary=vocabulary, mode=mode)
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        response = await client.post(INTERACTIONS_URL, headers={"x-goog-api-key": api_key}, json=body)
    if response.status_code != 200:
        raise CloudSTTError(f"HTTP {response.status_code}: {response.text[:200]}")
    return interaction_text(response.json())
