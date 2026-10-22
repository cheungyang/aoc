"""Shared test helpers for establishing an ExecutionContext.

Tools no longer accept a model-supplied `agent_id`; identity is derived from the ambient
ExecutionContext. Tests that exercise a tool therefore need to run inside one.
`envelope` reads an agent tool's reply (core/tooling/report.py) into its parts.

Also holds `llm_result`, a real LLMResult builder shared by the logging-handler,
token-usage-handler and logging-util tests, `GOLDEN_INPUT`, the structured reply
shared by the missing-speech and speech-coverage tests, `FakeTTS` for voice
tests that speak, and `FakeSpeakerModel` for tests of the voiceprint gate.
"""
import re
from contextlib import contextmanager

from core.runtime.execution_context import current_execution_context
from core.runtime.session_manager import SessionManager


def make_context(agent_id="test-agent", graph_id=None, source="job", channel=None, stateless=True):
    """Builds an ExecutionContext without entering it."""
    return SessionManager().get_session(
        agent_id=agent_id,
        source=source,
        channel=channel,
        stateless=stateless,
        graph_id=graph_id,
    )


@contextmanager
def execution_context(agent_id="test-agent", graph_id=None, source="job", channel=None, stateless=True):
    """Enters an ExecutionContext for the duration of the block."""
    ctx = make_context(
        agent_id=agent_id,
        graph_id=graph_id,
        source=source,
        channel=channel,
        stateless=stateless,
    )
    token = current_execution_context.set(ctx)
    try:
        yield ctx
    finally:
        current_execution_context.reset(token)


def envelope(response: str) -> dict:
    """An @agent_tool reply as {"status", "issues", "payload", "errors"}, plus "explain" when present.

    The payload is everything between its tags, so it may hold any text.
    """
    head, found, rest = response.partition("<payload>")
    payload, _, tail = rest.rpartition("</payload>")
    parts = {}
    for tag in ("status", "issues", "explain"):
        match = re.search(rf"<{tag}>(.*?)</{tag}>", head, re.DOTALL)
        if match:
            parts[tag] = match.group(1)
    if found:
        parts["payload"] = payload
    match = re.search(r"<errors>(.*?)</errors>", tail, re.DOTALL)
    if match:
        parts["errors"] = match.group(1)
    return parts


LLM_USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


def llm_result(usage=None, response_metadata=None, llm_output=None, generation_info=None, text="reply"):
    """A real LLMResult, shaped the way langchain_google_genai returns one."""
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    message = AIMessage(
        content=text,
        usage_metadata=usage,
        response_metadata=response_metadata or {},
    )
    gen = ChatGeneration(message=message, generation_info=generation_info)
    return LLMResult(generations=[[gen]], llm_output=llm_output)


USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}

# Shared by the missing-speech and speech-coverage tests: a numbered list with
# a heading, the canonical "nothing may be dropped" input.
GOLDEN_INPUT = """## Weekend options

1. **Point Reyes** - 2 hour drive, $0 entry, best in the morning fog.
2. **Mount Tam** - 45 minutes, $8 parking, steep but short.
3. **Muir Woods** - 1 hour, $15 plus a $9 reservation, book ahead.
4. **Stinson Beach** - 1 hour 15, free, windy after noon.
5. **Tennessee Valley** - 40 minutes, free, flat and stroller friendly.
"""


class FakeTTS:
    """Stands in for TTSEngine (core/voice/engines/tts.py): each stream records
    its voice and the chunks it was sent, and gives its speaker 20 ms of audio
    per chunk, so a queued speaker is ready at once."""

    def __init__(self):
        self.streams = []

    async def open(self, voice, speaker):
        stream = FakeTTSStream(voice, speaker)
        self.streams.append(stream)
        return stream

    def said(self):
        """What was spoken: one string per stretch of speech."""
        return [stream.text for stream in self.streams]


class FakeTTSStream:
    def __init__(self, voice, speaker):
        self.voice = voice
        self.speaker = speaker
        self.chunks = []
        self.finished = False
        self.cancelled = False

    @property
    def text(self):
        return " ".join(chunk.strip() for chunk in self.chunks)

    def send(self, text):
        self.chunks.append(text)
        self.speaker.write(bytes(1920))

    def finish(self):
        self.finished = True
        self.speaker.finish()

    def cancel(self):
        self.cancelled = True
        self.speaker.finish()


class FakeSpeakerModel:
    """Stands in for the voiceprint gate's network (speaker_gate.SpeakerModel).

    Its embedding of N ms of audio has similarity `score_at(N)` to the
    voiceprint [1, 0] (`FAKE_VOICEPRINT`); audio too short to embed gives None,
    as the real model's does. Records the length of every call.
    """

    name = "fake.onnx"

    def __init__(self, score_at=lambda ms: 0.9):
        self.score_at = score_at
        self.embedded = []
        self.loaded = False

    def _load(self):
        self.loaded = True

    def embed(self, pcm):
        import numpy as np
        from core.voice.listening.speaker_gate import MIN_EMBED_MS
        from core.voice.listening.utterance import BYTES_PER_MS

        ms = len(pcm) // BYTES_PER_MS
        self.embedded.append(ms)
        if ms < MIN_EMBED_MS:
            return None
        score = float(self.score_at(ms))
        return np.array([score, np.sqrt(max(0.0, 1 - score * score))])


FAKE_VOICEPRINT = [1.0, 0.0]
