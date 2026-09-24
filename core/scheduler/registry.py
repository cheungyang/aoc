"""Turns `agent.json` schedule entries into specs, and rejects the ones that aren't.

A schedule that does not implement the protocol never runs. Each entry is
validated on its own: one bad entry is rejected, logged and reported, and the
other schedules — and the rest of the process — carry on.

Accepted entry shapes:

    {"cron": "0 17 * * *", "kind": "prompt", "channel": "pkm-wiki",
     "precondition": {"type": "task_list_non_empty", "filter": "untriaged"},
     "session_policy": "stateless",
     "prompt": ["Triage untriaged tasks."]}

    {"cron": "*/5 * * * *", "kind": "script", "channel": "software-dev",
     "script": "coding_tick.py"}

The same validator backs the `schedule_validate` tool, so an agent editing
`agent.json` can check an entry before it saves it.
"""
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.scheduler.preconditions import PreconditionError, build_precondition
from core.scheduler.script_runner import check_script, parse_step
from core.scheduler.spec import (
    KIND_PROMPT,
    KIND_SCRIPT,
    PromptSchedule,
    ScheduleSpec,
    ScriptSchedule,
    SessionPolicy,
)

KINDS = (KIND_PROMPT, KIND_SCRIPT)

COMMON_KEYS = {"id", "cron", "kind", "enabled", "channel", "thread", "description", "precondition"}
PROMPT_KEYS = COMMON_KEYS | {"prompt", "session_policy"}
SCRIPT_KEYS = COMMON_KEYS | {"script"}


class ScheduleValidationError(ValueError):
    def __init__(self, errors: List[str]):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


@dataclass
class Rejection:
    """A schedule entry that was not registered, and exactly why."""
    agent_id: str
    index: int
    entry: Any
    errors: List[str]

    @property
    def channel(self) -> Optional[str]:
        """Where the alert goes: the entry's own channel, or nowhere (log only)."""
        if isinstance(self.entry, dict):
            channel = self.entry.get("channel")
            if isinstance(channel, str) and channel.strip():
                return channel.strip()
        return None

    @property
    def cron(self) -> str:
        return str(self.entry.get("cron")) if isinstance(self.entry, dict) else "?"

    @property
    def reason(self) -> str:
        return "; ".join(self.errors)

    def key(self) -> str:
        return f"{self.agent_id}#{self.index}:{self.reason}"

    def message(self) -> str:
        return (
            f"⚠️ Schedule rejected — `{self.agent_id}` schedules[{self.index}] "
            f"(cron `{self.cron}`) will not run: {self.reason}"
        )


@dataclass
class LoadResult:
    specs: List[ScheduleSpec] = field(default_factory=list)
    rejections: List[Rejection] = field(default_factory=list)


def _is_valid_cron(expr: Any) -> bool:
    if not isinstance(expr, str) or not expr.strip():
        return False
    try:
        from croniter import croniter
        return bool(croniter.is_valid(expr))
    except Exception:
        return False


def _parse_enabled(value: Any) -> Optional[bool]:
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no"):
        return False
    return None


def _prompt_text(value: Any) -> Optional[str]:
    if isinstance(value, str):
        return value if value.strip() else None
    if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
        text = "\n".join(value)
        return text if text.strip() else None
    return None


def _script_entries(value: Any) -> Optional[List[str]]:
    if isinstance(value, str) and value.strip():
        return [value]
    if isinstance(value, list) and value and all(isinstance(v, str) and v.strip() for v in value):
        return list(value)
    return None


def schedule_id_for(agent_id: str, entry: Dict[str, Any]) -> str:
    """Stable identity for per-schedule state.

    An explicit `id` wins. Otherwise it is derived from what the schedule *is*
    (kind, cron, script or prompt), not from its position, so reordering the
    array does not hand one schedule another's last-success time. Editing a
    schedule gives it a new identity, which errs towards running it once.
    """
    explicit = entry.get("id")
    if isinstance(explicit, str) and explicit.strip():
        return f"{agent_id}:{explicit.strip()}"
    kind = entry.get("kind")
    body = entry.get("script") if kind == KIND_SCRIPT else entry.get("prompt")
    digest = hashlib.sha1(
        json.dumps([kind, entry.get("cron"), entry.get("channel"), body], sort_keys=True).encode("utf-8")
    ).hexdigest()[:10]
    return f"{agent_id}:{kind}:{digest}"


def build_spec(
    agent_id: str, entry: Any, scripts_dir: Optional[str] = None
) -> ScheduleSpec:
    """Builds a spec from one entry, or raises ScheduleValidationError with every reason."""
    if not isinstance(entry, dict):
        raise ScheduleValidationError(["entry must be a JSON object"])

    errors: List[str] = []

    cron = entry.get("cron")
    if not _is_valid_cron(cron):
        errors.append(f"invalid or missing 'cron' ({cron!r})")

    enabled = _parse_enabled(entry.get("enabled"))
    if enabled is None:
        errors.append(f"'enabled' must be true or false, got {entry.get('enabled')!r}")

    for key in ("channel", "thread"):
        value = entry.get(key)
        if value is not None and not (isinstance(value, str) and value.strip()):
            errors.append(f"'{key}' must be a non-empty string")

    kind = entry.get("kind")
    if kind not in KINDS:
        errors.append(
            f"missing or unknown 'kind' ({kind!r}); must be one of: {', '.join(KINDS)}"
        )
        raise ScheduleValidationError(errors)

    allowed = PROMPT_KEYS if kind == KIND_PROMPT else SCRIPT_KEYS
    unknown = sorted(set(entry) - allowed)
    if unknown:
        hint = ""
        if kind == KIND_SCRIPT and "session_policy" in unknown:
            hint = " (session_policy applies to prompt schedules only)"
        errors.append(f"unknown key(s) for a {kind} schedule: {', '.join(unknown)}{hint}")

    precondition = None
    if "precondition" in entry:
        try:
            precondition = build_precondition(entry["precondition"])
        except PreconditionError as e:
            errors.append(f"precondition: {e}")
    elif kind == KIND_PROMPT:
        errors.append(
            "prompt schedules require a 'precondition' (a library type, or "
            "{\"type\": \"always\", \"reason\": \"...\"})"
        )

    common = dict(
        schedule_id=schedule_id_for(agent_id, entry),
        agent_id=agent_id,
        cron=cron,
        channel=entry.get("channel"),
        thread=entry.get("thread"),
        enabled=bool(enabled),
        precondition=precondition,
        entry=entry,
    )

    if kind == KIND_PROMPT:
        prompt = _prompt_text(entry.get("prompt"))
        if prompt is None:
            errors.append("'prompt' must be a non-empty string or list of strings")
        if not (isinstance(entry.get("channel"), str) and entry.get("channel").strip()):
            errors.append("prompt schedules require a 'channel' (the scheduled session is keyed by it)")
        try:
            policy = SessionPolicy.parse(entry.get("session_policy"))
        except ValueError as e:
            errors.append(str(e))
            policy = SessionPolicy.STATELESS
        if errors:
            raise ScheduleValidationError(errors)
        return PromptSchedule(prompt=prompt, session_policy=policy, **common)

    raw_steps = _script_entries(entry.get("script"))
    steps = []
    if raw_steps is None:
        errors.append("'script' must be a script name or a non-empty list of them")
    else:
        for raw in raw_steps:
            try:
                step = parse_step(raw)
            except ValueError as e:
                errors.append(f"script {raw!r}: {e}")
                continue
            errors.extend(check_script(step.script, scripts_dir))
            steps.append(step)
    if errors:
        raise ScheduleValidationError(errors)
    return ScriptSchedule(steps=steps, scripts_dir=scripts_dir, **common)


def validate_entry(agent_id: str, entry: Any, scripts_dir: Optional[str] = None) -> List[str]:
    """Every reason `entry` would be rejected. An empty list means it is valid."""
    try:
        build_spec(agent_id, entry, scripts_dir)
    except ScheduleValidationError as e:
        return e.errors
    return []


def load_agent_schedules(
    agent_id: str, config: Optional[Dict[str, Any]], scripts_dir: Optional[str] = None
) -> LoadResult:
    """Specs and rejections for one agent's `schedules` array."""
    result = LoadResult()
    schedules = (config or {}).get("schedules") or []
    if not isinstance(schedules, list):
        result.rejections.append(Rejection(agent_id, 0, schedules, ["'schedules' must be a list"]))
        return result

    seen: Dict[str, int] = {}
    for index, entry in enumerate(schedules):
        try:
            spec = build_spec(agent_id, entry, scripts_dir)
        except ScheduleValidationError as e:
            result.rejections.append(Rejection(agent_id, index, entry, e.errors))
            continue
        # Two identical entries would otherwise share one last-success record.
        count = seen.get(spec.schedule_id, 0)
        seen[spec.schedule_id] = count + 1
        if count:
            spec.schedule_id = f"{spec.schedule_id}#{count}"
        result.specs.append(spec)
    return result


def load_all(loader, scripts_dir: Optional[str] = None) -> LoadResult:
    """Every agent's schedules via an AgentsLoader-like object."""
    result = LoadResult()
    for agent_id in sorted(loader.list_agent_ids()):
        agent_result = load_agent_schedules(agent_id, loader.get_agent_config(agent_id), scripts_dir)
        result.specs.extend(agent_result.specs)
        result.rejections.extend(agent_result.rejections)
    return result
