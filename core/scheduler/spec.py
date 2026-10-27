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


RAW_LINES_SHOWN = 2  # the raw lines shown under each issue in a findings block or a notice


def listed_issues(issues: tuple, raw: tuple = ()) -> str:
    """Each issue as a bullet, with the first lines the scan couldn't read quoted under it.

    `raw` pairs an issue with its raw lines: ((issue, (line, ...)), ...).
    """
    lines_of = dict(raw)
    out = []
    for issue in issues:
        out.append(f"- {issue}")
        lines = lines_of.get(issue, ())
        out.extend(f"> {line}" for line in lines[:RAW_LINES_SHOWN])
        if len(lines) > RAW_LINES_SHOWN:
            out.append(f"> ... and {len(lines) - RAW_LINES_SHOWN} more")
    return "\n".join(out)


@dataclass(frozen=True)
class Findings:
    """What a scan precondition found, handed to the run it triggered.

    `PromptSchedule.build_prompt` appends it to the prompt, so the agent starts
    from what the check already read instead of reading it again. `issues` are
    what the scan couldn't read; the agent must pass them on, not guess around them.
    `raw` holds the lines behind each issue, so the agent can quote them.
    """
    source: str
    text: str
    issues: tuple = ()
    raw: tuple = field(default=(), compare=False)

    def as_block(self) -> str:
        from core.prompts.scheduler import FindingsPrompt

        issues = listed_issues(self.issues, self.raw) if self.issues else ""
        return FindingsPrompt(source=self.source, body=self.text.strip(), issues=issues).render()


@dataclass(frozen=True)
class Problems:
    """Why a scan couldn't decide, posted in its agent's channel (once per issue per day).

    A scan that skipped quietly because a file it reads changed shape would hide
    the problem; the runner posts these instead (`ScheduleRunner._post_problems`).
    `raw` holds the lines behind each issue: the notice quotes the first ones, so
    a reply asking which line needs no second look.
    """
    source: str
    issues: tuple
    raw: tuple = field(default=(), compare=False)

    def message(self, description: str = "") -> str:
        what = description or f"The {self.source} scan"
        return f"⚠️ {what} couldn't run normally:\n{listed_issues(self.issues, self.raw)}\nReply here and I'll help fix it."


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
        priority: Any = None,
        label: Optional[str] = None,
        entry: Optional[Dict[str, Any]] = None,
    ):
        from core.storage.channel_inbox import Priority

        self.schedule_id = schedule_id
        self.agent_id = agent_id
        self.cron = cron
        self.channel = channel
        self.thread = thread
        self.enabled = enabled
        self.precondition = precondition
        # How its posts reach you in voice briefings (core/voice/briefing).
        self.priority = Priority.parse(priority)
        self.label = label
        self.entry = dict(entry or {})

    @abstractmethod
    def has_work(self, ctx: ScheduleContext) -> WorkDecision:
        """Cheap, no LLM. Returns whether this run should happen, and why."""

    def on_success(self, ctx: ScheduleContext) -> None:
        """Called after a successful run; the runner then records last_success_at."""

    def on_failure(self, ctx: ScheduleContext) -> None:
        """Called after a run that failed; last_success_at stays where it was."""

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
    """An LLM turn for an agent.

    `thinking_level`, when set, is the reasoning depth for this run's model
    calls; None leaves the agent's scheduled level in charge.
    """

    kind = KIND_PROMPT

    def __init__(
        self,
        *,
        prompt: str,
        session_policy: SessionPolicy = SessionPolicy.STATELESS,
        thinking_level: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.prompt = prompt
        self.session_policy = SessionPolicy.parse(session_policy)
        self.thinking_level = thinking_level

    def has_work(self, ctx: ScheduleContext) -> WorkDecision:
        return self._check_precondition(ctx)

    def build_prompt(self, ctx: ScheduleContext, decision: Optional[WorkDecision] = None) -> str:
        """Only called when has_work() is True.

        A scan precondition's findings (`decision.detail`) are appended in a
        `<findings>` block, so the run starts from what the check already read.
        """
        if decision is not None and isinstance(decision.detail, Findings):
            return f"{self.prompt}\n\n{decision.detail.as_block()}"
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
