"""The schedule protocol: what every scheduled thing must answer.

The runner only ever talks to `ScheduleSpec`. It asks `has_work()` first — a
cheap, deterministic check with no model behind it — and only a schedule that
says yes is allowed to spend a request. That is the whole point of this module:
"if empty, terminate silently" used to live in prompts, which saved the Discord
message but not the request, because the model had already been paid to read
its prompt before deciding there was nothing to do.

Two concrete kinds, because they really are different things:

- `PromptSchedule` is an LLM turn for an agent. It has a prompt and a session
  policy.
- `ScriptSchedule` runs one or more scripts from `scripts/`. It has no prompt
  and no session; each script answers `has_work()` for itself.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


KIND_PROMPT = "prompt"
KIND_SCRIPT = "script"


class SessionPolicy(Enum):
    """What a scheduled prompt remembers between runs.

    STATELESS is the default: the run starts with an empty conversation, so the
    nightly job stops replaying every previous night. PERSISTENT keeps today's
    behaviour — the scheduled session accumulates, bounded only by the pruner —
    for the agents whose runs genuinely build on each other.
    """
    STATELESS = "stateless"
    PERSISTENT = "persistent"

    @classmethod
    def parse(cls, value: Any, default: "SessionPolicy" = None) -> "SessionPolicy":
        """Reads a policy from config. Unknown values raise rather than guess."""
        if value is None:
            return default if default is not None else cls.STATELESS
        if isinstance(value, cls):
            return value
        text = str(value).strip().lower()
        for member in cls:
            if member.value == text:
                return member
        allowed = ", ".join(m.value for m in cls)
        raise ValueError(f"unknown session_policy '{value}' (allowed: {allowed})")


@dataclass(frozen=True)
class WorkDecision:
    """The answer to "is there work?", with the reason that goes in the log.

    `detail` carries anything the runner needs to act on the decision without
    evaluating it twice — for a script schedule, the steps that have work.
    Unpacks as `(has_work, reason)` so plain tuple-returning checks and this
    class are interchangeable.
    """
    has_work: bool
    reason: str = ""
    detail: Any = None

    def __bool__(self) -> bool:
        return bool(self.has_work)

    def __iter__(self):
        yield self.has_work
        yield self.reason

    @classmethod
    def coerce(cls, value: Any) -> "WorkDecision":
        """Accepts a WorkDecision, a `(bool, reason)` tuple, or a bare bool."""
        if isinstance(value, cls):
            return value
        if isinstance(value, tuple) and len(value) == 2:
            return cls(bool(value[0]), str(value[1]))
        if isinstance(value, bool):
            return cls(value, "")
        raise TypeError(
            f"has_work() must return (bool, reason); got {type(value).__name__}"
        )


@dataclass
class ScheduleContext:
    """What a schedule knows when it is asked whether it has work.

    `last_success_at` is the start time of the last run that *succeeded*
    (epoch seconds), or None if it never has. It is only ever advanced on
    success, so a failed run leaves its work visible to the next check.
    """
    schedule_id: str
    agent_id: str
    now: float
    last_success_at: Optional[float] = None
    channel: Optional[str] = None
    thread: Optional[str] = None
    entry: Dict[str, Any] = field(default_factory=dict)


class ScheduleSpec(ABC):
    """Every schedule. The runner only ever talks to this."""

    kind: str = ""

    def __init__(
        self,
        *,
        schedule_id: str,
        agent_id: str,
        cron: str,
        channel: Optional[str] = None,
        thread: Optional[str] = None,
        enabled: bool = True,
        precondition: Any = None,
        entry: Optional[Dict[str, Any]] = None,
    ):
        self.schedule_id = schedule_id
        self.agent_id = agent_id
        self.cron = cron
        self.channel = channel
        self.thread = thread
        self.enabled = enabled
        self.precondition = precondition
        self.entry = dict(entry or {})

    @abstractmethod
    def has_work(self, ctx: ScheduleContext) -> WorkDecision:
        """Cheap, no LLM. Returns whether this run should happen, and why."""

    def on_success(self, ctx: ScheduleContext) -> None:
        """Called after a successful run; the runner then records last_success_at."""

    def on_skip(self, ctx: ScheduleContext, reason: str) -> None:
        """Called when has_work() said no."""

    def _check_precondition(self, ctx: ScheduleContext) -> WorkDecision:
        if self.precondition is None:
            return WorkDecision(True, "no precondition")
        return WorkDecision.coerce(self.precondition.evaluate(ctx))

    def describe(self) -> str:
        where = f"#{self.channel}" if self.channel else "no channel"
        return f"{self.agent_id} [{self.kind}] '{self.cron}' -> {where}"


class PromptSchedule(ScheduleSpec):
    """An LLM turn for an agent."""

    kind = KIND_PROMPT

    def __init__(
        self,
        *,
        prompt: str,
        session_policy: SessionPolicy = SessionPolicy.STATELESS,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.prompt = prompt
        self.session_policy = SessionPolicy.parse(session_policy)

    def has_work(self, ctx: ScheduleContext) -> WorkDecision:
        return self._check_precondition(ctx)

    def build_prompt(self, ctx: ScheduleContext) -> str:
        """Only called when has_work() is True."""
        return self.prompt


@dataclass(frozen=True)
class ScriptStep:
    """One script invocation: a file in `scripts/` plus its arguments."""
    script: str
    args: tuple = ()

    def label(self) -> str:
        return " ".join((self.script,) + tuple(self.args))


class ScriptSchedule(ScheduleSpec):
    """One or more non-LLM scripts, run in order. No prompt, no session.

    Several steps share one schedule when order matters — `wiki_scanner.py`
    reads the knowledge store `sync_knowledge.py` has just refreshed — which
    separate schedules on the same cron could not guarantee.
    """

    kind = KIND_SCRIPT

    def __init__(self, *, steps: List[ScriptStep], scripts_dir: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.steps = list(steps)
        self.scripts_dir = scripts_dir

    @property
    def script(self) -> str:
        """The first script, for logs and single-step schedules."""
        return self.steps[0].script if self.steps else ""

    def steps_with_work(self, ctx: ScheduleContext) -> List[tuple]:
        """[(step, WorkDecision)] for every step whose own has_work() said yes."""
        from core.scheduler.script_runner import call_has_work

        selected = []
        for step in self.steps:
            decision = call_has_work(step, ctx, scripts_dir=self.scripts_dir)
            if decision:
                selected.append((step, decision))
        return selected

    def has_work(self, ctx: ScheduleContext) -> WorkDecision:
        gate = self._check_precondition(ctx)
        if not gate:
            return gate

        selected = self.steps_with_work(ctx)
        if not selected:
            return WorkDecision(False, "no script reported work", detail=[])
        reasons = "; ".join(f"{step.script}: {decision.reason}" for step, decision in selected)
        return WorkDecision(True, reasons, detail=[step for step, _ in selected])
