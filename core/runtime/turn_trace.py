"""Per-turn latency trace for voice turns.

Every mark is milliseconds since the user's **last voiced frame**, so the
numbers read as what the listener lives through: "audio started 4.2 s after I
stopped talking". The 900 ms end-of-speech wait is therefore inside the trace,
not hidden before it.

Stages, in the order a turn normally reaches them (first occurrence wins, so a
later token never moves `first_token`):

    speech_end      the anchor (0 ms): the last frame the VAD heard as speech
    vad_final       the VAD declared the utterance over (the silence wait)
    stt_done        the transcript is ready
    agent_start     the agent run was started
    first_token     the first model text arrived
    first_chunk     the first speakable chunk was cut from the stream
    tts_first_byte  the first synthesized audio is ready
    audio_start     the first answer audio started playing
    turn_end        the pipeline finished (all audio queued or played)

Repeated events (`tool_start`, `tool_end`, `model_call` with the thinking
level it was sent with) are kept in order as `events`. A `prune_wait` event
records the milliseconds a turn waited for the previous turn's history prune;
it appears only when there was one to wait for.

VoiceManager opens one trace per turn with `start_turn_trace`, which binds it
to the running task's context; code inside the turn (the model wrapper, the
pipeline, playback callbacks) reaches it through `current_turn_trace()`. With
no trace bound, every helper is a no-op, so text turns pay nothing.

`finish()` writes one `[TurnTrace]` log line and stores the trace as a
`turn_trace` row in the session's table, next to the turn's token rows, so
p50/p90 per stage can be computed with the same tooling as token usage.
"""
import contextvars
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

STAGES = (
    "speech_end",
    "vad_final",
    "stt_done",
    "agent_start",
    "first_token",
    "first_chunk",
    "tts_first_byte",
    "audio_start",
    "turn_end",
)

_current_trace: contextvars.ContextVar[Optional["TurnTrace"]] = contextvars.ContextVar(
    "current_turn_trace", default=None
)


@dataclass
class TurnTrace:
    """Timings for one voice turn. Mutable and shared by everything in the turn."""

    agent_id: str
    turn_id: int
    anchor: float  # time.monotonic() of the last voiced frame
    marks: Dict[str, int] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)
    finished: bool = False

    def __post_init__(self):
        self.marks.setdefault("speech_end", 0)

    def _ms(self, at: Optional[float] = None) -> int:
        return round(((time.monotonic() if at is None else at) - self.anchor) * 1000)

    def mark(self, stage: str, at: Optional[float] = None) -> None:
        """Records `stage` once; later calls for the same stage are ignored."""
        if stage not in STAGES:
            raise ValueError(f"Unknown turn stage {stage!r}; expected one of {STAGES}.")
        if self.finished:
            return  # recorded already; a straggler (late playback callback) can't change it
        self.marks.setdefault(stage, self._ms(at))

    def event(self, kind: str, **data: Any) -> None:
        """Records a repeatable event (a tool call, a model call) at the current time."""
        if self.finished:
            return
        self.events.append({"t": self._ms(), "kind": kind, **data})

    def model_call(self, agent_id: Optional[str], model: str, thinking_level: Optional[str]) -> None:
        self.event("model_call", agent=agent_id, model=model, thinking=thinking_level)

    def stage_deltas(self) -> Dict[str, int]:
        """Milliseconds spent between consecutive reached stages, keyed by the later one."""
        deltas, previous = {}, None
        for stage in STAGES:
            if stage not in self.marks:
                continue
            if previous is not None:
                deltas[stage] = self.marks[stage] - self.marks[previous]
            previous = stage
        return deltas

    def summary(self) -> Dict[str, Any]:
        return {
            "agent": self.agent_id,
            "turn": self.turn_id,
            "ttfa_ms": self.marks.get("audio_start"),
            "marks": dict(self.marks),
            "deltas": self.stage_deltas(),
            "events": list(self.events),
            **self.meta,
        }

    def finish(self, session_id: Optional[str] = None, surface: str = "voice") -> Dict[str, Any]:
        """Marks `turn_end`, logs the trace, and stores it next to the turn's token rows.

        Idempotent: a turn cancelled after finishing is not recorded twice.
        Storage is best effort -- a trace must never break the turn it measures.
        """
        if self.finished:
            return self.summary()
        self.mark("turn_end")
        self.finished = True
        summary = self.summary()
        print(f"[TurnTrace] {json.dumps(summary, separators=(',', ':'), default=str)}")
        if session_id:
            try:
                from core.knowledge.memory.sqlite_session_store import SqliteSessionStore
                SqliteSessionStore().append_turn_trace(session_id, summary, surface=surface)
            except Exception as e:
                print(f"[TurnTrace] could not store trace for {session_id}: {e}")
        return summary


def start_turn_trace(agent_id: str, turn_id: int, speech_end_at: Optional[float] = None, **meta: Any) -> TurnTrace:
    """Opens a trace for the current task. `speech_end_at` is a time.monotonic() value."""
    trace = TurnTrace(
        agent_id=agent_id,
        turn_id=turn_id,
        anchor=time.monotonic() if speech_end_at is None else speech_end_at,
        meta=dict(meta),
    )
    _current_trace.set(trace)
    return trace


def current_turn_trace() -> Optional[TurnTrace]:
    """The trace of the voice turn this code is running in, or None."""
    return _current_trace.get()


def detach_turn_trace() -> None:
    """Unbinds the trace in the current context.

    For background work started during a turn but not part of it (the
    post-turn history prune): run it in a copied context with this applied, so
    its model calls aren't counted as the turn's.
    """
    _current_trace.set(None)
