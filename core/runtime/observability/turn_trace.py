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

`Stage` names the stages and `Outcome` how a turn ended (`meta["outcome"]`;
none means it was answered), so a misspelled one fails at import.

Repeated events are kept in order as `events`:

- `cue`: the "heard you" sound was started;
- `tool_start`, `tool_end`;
- `model_call`: a model call was sent, numbered (`call`) within the turn,
  with its model and the thinking level it was sent with; then its
  `model_reply` events, with the same number: `first_chunk` when it began to
  stream, `done` when it ended. So each round trip's own latency is there,
  not only the turn's first token;
- `model_retry`: the latest call was tried again, `by` the SDK itself (a
  `status` of 408, 429, 500, 502 or 504, or none for a dropped connection)
  or by the model wrapper (503), with the wait before it (`wait_s`) when known
  (core/runtime/model/retry.py);
- `model_hedge`: the stall guard sent a call again, having had no first chunk
  by its deadline (`phase` sent, `after_s`); then which request answered first
  (`phase` won, `by` first or second);
- `repeats_dropped`: the copies of the user's words a cancelled turn left
  unanswered, which this turn's message repeats, were dropped from an agent's
  history (`agent`, `count`; graphs/main, core/runtime/history/repeats.py);
- `prune_wait`: the milliseconds a turn waited for the previous turn's
  history prune, only when there was one to wait for.

How the words were heard is in `meta` (core/channel/voice/turn.py `record_stt`): the
recognizer (`stt`), the live session's own timings (`stt_live_ms`, from speech
onset; `stt_final_after_end_ms`), why it failed (`stt_error`), how long batch
transcription took (`stt_transcribe_ms`), and why nothing could transcribe the
words (`stt_failed`). `loop_lag` is how long the event loop was stalled during
the turn, when the probe runs (loop_lag.py).

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

from core.runtime.observability.loop_lag import lag_between

class Stage:
    """The stages above, by name: `trace.mark(Stage.STT_DONE)`."""
    SPEECH_END = "speech_end"
    VAD_FINAL = "vad_final"
    STT_DONE = "stt_done"
    AGENT_START = "agent_start"
    FIRST_TOKEN = "first_token"
    FIRST_CHUNK = "first_chunk"
    TTS_FIRST_BYTE = "tts_first_byte"
    AUDIO_START = "audio_start"
    TURN_END = "turn_end"


STAGES = (
    Stage.SPEECH_END,
    Stage.VAD_FINAL,
    Stage.STT_DONE,
    Stage.AGENT_START,
    Stage.FIRST_TOKEN,
    Stage.FIRST_CHUNK,
    Stage.TTS_FIRST_BYTE,
    Stage.AUDIO_START,
    Stage.TURN_END,
)


class Outcome:
    """How a turn ended, in `meta["outcome"]`. A trace without one was answered."""
    CANCELLED = "cancelled"          # its task was cancelled: a barge-in, or a newer turn
    ERROR = "error"                  # the pipeline raised
    SUPERSEDED = "superseded"        # a newer turn started before it finished
    NO_TRANSCRIPT = "no_transcript"  # noise, not words
    STT_FAILED = "stt_failed"        # words, but no recognizer could say what: "didn't catch that"
    STOP_HOLD = "stop_hold"          # "wait": the reply stopped, the words kept
    STOP_CANCEL = "stop_cancel"      # "stop": the reply stopped, the words dropped
    HOOK = "hook"                    # a voice hook answered it ("next agent")
    FOCUS_SWITCH = "focus_switch"    # the switcher answered it: a switch, "where am I?"
    HANG_UP = "hang_up"              # "hanging up": the voice session ended

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
    calls: int = 0  # model calls sent so far: the last one's number

    def __post_init__(self):
        self.marks.setdefault(Stage.SPEECH_END, 0)

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

    def model_call(self, agent_id: Optional[str], model: str, thinking_level: Optional[str]) -> int:
        """Records a model call as it's sent; returns its number, for its `model_reply` events."""
        self.calls += 1
        self.event("model_call", call=self.calls, agent=agent_id, model=model, thinking=thinking_level)
        return self.calls

    def model_reply(self, call: int, phase: str) -> None:
        """Model call `call` began to stream (`first_chunk`) or ended (`done`)."""
        self.event("model_reply", call=call, phase=phase)

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
        self.mark(Stage.TURN_END)
        lag = lag_between(self.anchor, time.monotonic())
        if lag is not None:
            self.meta["loop_lag"] = lag
        self.finished = True
        summary = self.summary()
        print(f"[TurnTrace] {json.dumps(summary, separators=(',', ':'), default=str)}")
        if session_id:
            try:
                from core.storage.sessions import SessionStore
                SessionStore().append_turn_trace(session_id, summary, surface=surface)
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
