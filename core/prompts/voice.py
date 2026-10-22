"""Voice prompts: the <speech> contract on turns the user hears, and the two
small worker tasks voice relies on.

Two partials in templates/voice/ hold the shared rules:
    _speech_rules.md   the <speech> rules every voice block shares: live voice
                       turns, scheduled posts, and the speech writer's conversions
    _question_rule.md  marks the question a post waits on, so the voice can
                       replay exactly that question later; only posts that may
                       be heard later need it
"""
import contextvars
import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from core.prompts.base import Prompt, WorkerPrompt


class VoiceModePrompt(Prompt):
    """On a turn the user will hear, appended after the channel block (see
    `get_voice_mode_prompt`). The voice pipeline speaks the <speech> block and
    posts everything after it to the text channel (core/voice/speech/reply.py)."""

    template = "voice/voice_mode"


class ScheduledVoiceModePrompt(Prompt):
    """On a scheduled run whose post may be heard later (see
    `scheduled_speech_scope`). The <speech> block is stored with the post's
    channel inbox row (core/inbox) and never posted; the channel gets the rest."""

    template = "voice/scheduled_voice_mode"


# The agent whose scheduled run writes a speech version of its post. Set by
# the schedule runner (unless the schedule's priority is IGNORE) around the run.
_scheduled_speech_agent = contextvars.ContextVar("scheduled_speech_agent", default=None)


@contextmanager
def scheduled_speech_scope(agent_id: str):
    """Within this block, `agent_id`'s scheduled run gets the scheduled
    <voice_mode> block. Agents it calls don't: what they write goes back to
    it, not to the channel."""
    token = _scheduled_speech_agent.set(agent_id)
    try:
        yield
    finally:
        _scheduled_speech_agent.reset(token)


def get_voice_mode_prompt() -> str:
    """The <voice_mode> block, on turns the user will hear.

    That is a voice-surface turn of an agent that answers the user: the room's
    agent, or one it hands the turn to (which inherits the voice surface).
    Internal workers -- stateless agents such as graph-worker-low -- inherit it
    too, but what they write goes back to their caller, not to the speaker, so
    they never get the block.

    A scheduled run gets the scheduled variant inside `scheduled_speech_scope`:
    its post may be heard later, from the channel inbox.
    """
    from core.runtime.execution_context import try_context
    sess = try_context()
    if sess is None or sess.is_stateless():
        return ""
    surface = sess.get_surface()
    if surface == "voice":
        return VoiceModePrompt().render()
    if surface == "scheduled" and sess.agent_id and sess.agent_id == _scheduled_speech_agent.get():
        return ScheduledVoiceModePrompt().render()
    return ""


Speech = Tuple[Optional[str], Optional[str]]

# A worker that wraps its answer in its usual IPC envelope instead of <speech>.
_PAYLOAD_TAG = re.compile(r"</?payload\s*>", re.IGNORECASE)


@dataclass
class SpeechWriterPrompt(WorkerPrompt):
    """A Discord post, rewritten as what its author says aloud, under the same
    rules as <voice_mode>, <question> included (core/inbox/speech_writer.py).
    The answer is the speech version and its marked question."""

    template = "voice/speech_writer"
    caller = "speech-writer"
    timeout = 20.0

    name: str
    channel: str
    post: Optional[str]

    def values(self):
        return {**super().values(), "post": (self.post or "").strip()}

    def parse(self, result) -> Speech:
        return parse_speech(result.response)


def parse_speech(reply: Any) -> Speech:
    """The speech version in a reply, and its marked question. `reply` is the
    worker's parsed AgentResponse, or raw text. A reply that forgot the
    <speech> tags is taken whole."""
    from core.channel.response_parser import AgentResponse, split_question

    if isinstance(reply, list):
        reply = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in reply)
    if isinstance(reply, str):
        reply = AgentResponse.from_string(reply) if reply.strip() else None
    if not isinstance(reply, AgentResponse):
        return None, None
    if reply.speech:
        return reply.speech, reply.question
    return split_question(_PAYLOAD_TAG.sub("", reply.text or "").strip() or None)


@dataclass
class NearMissPrompt(WorkerPrompt):
    """Which switch target a voice phrase most likely means, when it names none
    exactly (core/voice/room/near_miss.py). The answer is the worker's reply,
    for the caller to check against its targets.

    A near miss waits on this call before anything is said, so it's kept short;
    on timeout the phrase is treated as an ordinary request.
    """

    template = "voice/near_miss"
    caller = "near-miss"
    timeout = 4.0

    phrase: str
    targets: str  # one line each: key | who answers | channel | what they do
