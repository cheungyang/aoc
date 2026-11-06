"""What the coding queues hold that needs Elephant's decision.

The coding graph records every outcome in the project's manifest
(pkm/wiki/software/<project>/build_request.json): each task's status, stage
and last error, and at the top level a `tick_error` for a failure that stops
ticks before they claim any task and a `deployment` for where the project's
default branch has got to. The scan (preconditions/coding_attention.py)
loads each manifest and hands it here: pure functions over the loaded data,
with the time passed in.

Each item has a kind and an event time, when it happened. The scan wakes
Elephant only for items whose event is newer than his last successful check,
and briefs him on the others as still open.

    halt            a task the graph halted; its last error says why
    review_budget   a halt with review feedback still pending: the review
                    arrived after the implement budget was spent
    tick_error      ticks stop before they claim any task: preflight, or the
                    repository
    deploy_failed   the project's deployment failed: the deploy itself, its
                    backend's configuration, or no answer before the timeout
    review_dormant  a PR awaiting review that the tick stopped checking
                    `remind_after_hours` ago or more; due again every
                    `remind_after_hours`

A halted task's item also says how many fixes Elephant has made to it
(`planner_fixes`) against `max_fixes_per_task`.

Left out: done tasks, tasks the user failed or skipped (status `failed` or
`blocked`), work still in flight, which the graph retries itself, and a
deployment still pending or live.
"""
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Sequence, Tuple

HALT = "halt"
REVIEW_BUDGET = "review_budget"
TICK_ERROR = "tick_error"
DEPLOY_FAILED = "deploy_failed"
REVIEW_DORMANT = "review_dormant"
KINDS = (HALT, REVIEW_BUDGET, TICK_ERROR, DEPLOY_FAILED, REVIEW_DORMANT)

# The statuses a task waits in until its dependencies are done (graphs/coding/utils/dag.py).
RUNNABLE_STATUSES = ("queued", "pending")
# How much of an error and of the review feedback the findings quote.
MESSAGE_CHARS = 600
FEEDBACK_ITEMS = 5
FEEDBACK_CHARS = 300
# How many new items the run's reason names before it counts the rest.
REASON_ITEMS = 3
HOUR = 3600
# How long a reopened check lasts: the tick's own review window (graphs/coding/nodes/publish.py).
REOPEN_SECONDS = HOUR


@dataclass(frozen=True)
class Item:
    """One thing in a coding queue that needs Elephant (module docstring)."""
    project: str
    kind: str
    event_at: float                      # when it happened, epoch seconds
    task_id: str = ""                    # empty for a tick error or a deploy: it belongs to the project
    stage: str = ""                      # the task's stage; a tick error's step; a deploy's backend
    attempts: str = ""                   # "implement 3, push 1"
    error: str = ""                      # the last error's kind and stage: "llm at implement"
    message: str = ""                    # the error's message, clipped
    feedback: Tuple[str, ...] = ()       # review comments the worker hasn't acted on yet
    pr_url: str = ""
    spec_path: str = ""
    waiting: Tuple[str, ...] = ()        # queued tasks that can't start until this one is done
    fixes: int = 0                       # Elephant's fixes so far (`planner_fixes`)
    max_fixes: int = 0                   # `max_fixes_per_task`
    polling_stopped_at: Optional[float] = None  # a waiting PR's poll_until
    reopen_until: Optional[float] = None        # the poll_until that would reopen its check
    log_url: str = ""                    # a failed deploy's log: the workflow run, the deployment

    @property
    def name(self) -> str:
        """`project/task`, or the project alone for a tick error or a deploy."""
        return f"{self.project}/{self.task_id}" if self.task_id else self.project

    @property
    def at_fix_limit(self) -> bool:
        return self.max_fixes > 0 and self.fixes >= self.max_fixes

    def is_new(self, last_success_at: Optional[float]) -> bool:
        """Whether it happened after Elephant's last successful check: always, before his first."""
        return last_success_at is None or self.event_at > last_success_at

    def summary(self, now: float) -> str:
        """The item in one line, for the still-open list."""
        if self.kind in (TICK_ERROR, DEPLOY_FAILED):
            first = (self.message.splitlines() or [""])[0]
            return f"{self.name} · {self.kind} · since {ago(now - self.event_at)} ago ({self.stage}): {_clip(first, 120)}"
        if self.kind == REVIEW_DORMANT:
            pr = f" · {self.pr_url}" if self.pr_url else ""
            return f"{self.name} · {self.kind} · not checked for {ago(now - self._stopped)}{pr}"
        error = f" ({self.error})" if self.error else ""
        fixes = f" · fixes {self.fixes} of {self.max_fixes}" if self.fixes else ""
        return f"{self.name} · {self.kind} · halted {ago(now - self.event_at)} ago{error}{fixes}"

    def details(self, now: float) -> List[str]:
        """The item's lines in the findings, when it's new."""
        if self.kind == TICK_ERROR:
            lines = [f"ticks stop at {self.stage or 'an unknown step'} before claiming any task, "
                     f"since {ago(now - self.event_at)} ago:"]
            return lines + self.message.splitlines()
        if self.kind == DEPLOY_FAILED:
            kind = f" ({self.error})" if self.error else ""
            lines = [f"the deployment to {self.stage or 'its backend'} failed {ago(now - self.event_at)} ago{kind}:"]
            lines += self.message.splitlines()
            return lines + ([f"log: {self.log_url}"] if self.log_url else [])
        if self.kind == REVIEW_DORMANT:
            lines = [f"awaiting review; the tick stopped checking it {ago(now - self._stopped)} ago"]
        else:
            attempts = f" · attempts: {self.attempts}" if self.attempts else ""
            lines = [f"halted at stage {self.stage or 'unknown'} {ago(now - self.event_at)} ago{attempts}"]
            message = self.message.splitlines()
            if message:
                lines.append(f"last error ({self.error or 'unclassified'}): {message[0]}")
                lines += [f"  {line}" for line in message[1:]]
            else:
                lines.append("last error: none recorded")
            if self.feedback:
                lines.append("review feedback still pending:")
                lines += [f"- {comment}" for comment in self.feedback]
        if self.pr_url:
            lines.append(f"PR: {self.pr_url}")
        if self.spec_path:
            lines.append(f"spec: {self.spec_path}")
        if self.waiting:
            lines.append(f"waiting behind it: {', '.join(self.waiting)}")
        if self.kind in (HALT, REVIEW_BUDGET):
            limit = "fix limit reached" if self.at_fix_limit else "fixes so far"
            lines.append(f"{limit}: {self.fixes} of {self.max_fixes}")
        if self.reopen_until is not None:
            lines.append(f"reopen value: poll_until {int(self.reopen_until)} (an hour from this check)")
        return lines

    @property
    def _stopped(self) -> float:
        return self.polling_stopped_at if self.polling_stopped_at is not None else self.event_at


def queue_items(manifest: Mapping[str, Any], project: str, now: float, rules: Mapping[str, Any]) -> List[Item]:
    """What in one project's manifest needs Elephant: its tick error and failed deploy first, then its tasks in queue order.

    `rules` holds `remind_after_hours` and `max_fixes_per_task` (lib/rules.py).
    """
    items: List[Item] = []
    for project_item in (_tick_error(manifest, project), _deploy_failed(manifest, project)):
        if project_item is not None:
            items.append(project_item)
    queue = tasks(manifest)
    for task in queue:
        status = task.get("status")
        if status == "halted":
            items.append(_halted(task, project, queue, int(rules["max_fixes_per_task"])))
        elif status == "awaiting_review":
            stopped = _number(task.get("poll_until"))
            due = dormant_event(stopped, now, rules["remind_after_hours"])
            if due is not None:
                items.append(_dormant(task, project, queue, due, stopped, now))
    return items


def dormant_event(poll_until: Optional[float], now: float, every_hours: float) -> Optional[float]:
    """When the latest reminder about a PR the tick stopped checking fell due; None before the first.

    The first falls due `every_hours` after `poll_until`, when the tick stopped
    checking the PR, and another every `every_hours` after that (0: never).
    Without a `poll_until` the tick still checks the PR on every run, so none
    falls due.
    """
    if poll_until is None or every_hours <= 0:
        return None
    step = every_hours * HOUR
    first = poll_until + step
    if now < first:
        return None
    return first + ((now - first) // step) * step


def waiting_behind(queue: Sequence[Mapping[str, Any]], task_id: str) -> Tuple[str, ...]:
    """The queued tasks that can't start until `task_id` is done: its dependents, theirs, and so on."""
    blocked = {task_id}
    waiting: List[str] = []
    changed = True
    while changed:
        changed = False
        for task in queue:
            other = task.get("task_id")
            if other in blocked or task.get("status") not in RUNNABLE_STATUSES:
                continue
            if any(dependency in blocked for dependency in task.get("dependencies") or []):
                blocked.add(other)
                waiting.append(str(other))
                changed = True
    return tuple(waiting)


def describe(new: Sequence[Item]) -> str:
    """The run's reason, for the scheduler's log: what's new, by name and kind."""
    shown = "; ".join(f"{item.name} {item.kind}" for item in new[:REASON_ITEMS])
    more = f" and {len(new) - REASON_ITEMS} more" if len(new) > REASON_ITEMS else ""
    return f"{len(new)} new: {shown}{more}"


def findings(new: Sequence[Item], still_open: Sequence[Item], now: float) -> str:
    """The findings: each new item in full, then the still-open ones in a line each."""
    lines = [f"New since your last check ({len(new)}):"]
    for number, item in enumerate(new, 1):
        lines.append(f"{number}. {item.name} · {item.kind}")
        lines += [f"   {line}" for line in item.details(now)]
    if still_open:
        lines += ["", f"Still open from before ({len(still_open)}):"]
        lines += [f"- {item.summary(now)}" for item in still_open]
    return "\n".join(lines)


def tasks(manifest: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    """The manifest's queue entries that name a task."""
    queue = manifest.get("queue") if isinstance(manifest, Mapping) else None
    return [task for task in (queue or []) if isinstance(task, Mapping) and task.get("task_id")]


def ago(seconds: float) -> str:
    """A span of time in words: "40 min", "5 h", "3 d"."""
    seconds = max(0.0, float(seconds))
    if seconds < HOUR:
        return f"{max(1, int(seconds // 60))} min"
    if seconds < 48 * HOUR:
        return f"{int(seconds // HOUR)} h"
    return f"{int(seconds // (24 * HOUR))} d"


def _tick_error(manifest: Mapping[str, Any], project: str) -> Optional[Item]:
    error = manifest.get("tick_error") if isinstance(manifest, Mapping) else None
    if not isinstance(error, Mapping):
        return None
    message = str(error.get("message") or "").strip()
    if not message:
        return None
    return Item(project=project, kind=TICK_ERROR, event_at=_number(error.get("at")) or 0.0,
                stage=str(error.get("stage") or ""), message=_clip(message, MESSAGE_CHARS))


def _deploy_failed(manifest: Mapping[str, Any], project: str) -> Optional[Item]:
    """The project's deployment, when it failed; it stays failed until a merge or `redeploy` asks again."""
    deployment = manifest.get("deployment") if isinstance(manifest, Mapping) else None
    if not isinstance(deployment, Mapping) or deployment.get("status") != "failed":
        return None
    error = deployment.get("last_error")
    error = error if isinstance(error, Mapping) else {}
    return Item(
        project=project,
        kind=DEPLOY_FAILED,
        event_at=_number(error.get("at")) or _number(deployment.get("updated_at")) or 0.0,
        stage=str(deployment.get("backend") or ""),
        error=str(error.get("kind") or ""),
        message=_clip(str(error.get("message") or "no reason recorded").strip(), MESSAGE_CHARS),
        log_url=str(deployment.get("log_url") or ""),
    )


def _halted(task: Mapping[str, Any], project: str, queue: Sequence[Mapping[str, Any]], max_fixes: int) -> Item:
    error = _last_error(task)
    feedback = _feedback(task)
    kind = REVIEW_BUDGET if feedback and error.get("kind") == "llm" else HALT
    return Item(
        project=project,
        kind=kind,
        event_at=_number(error.get("at")) or _number(task.get("updated_at")) or 0.0,
        task_id=str(task["task_id"]),
        stage=str(task.get("stage") or ""),
        attempts=_attempts(task),
        error=" at ".join(str(part) for part in (error.get("kind"), error.get("stage")) if part),
        message=_clip(str(error.get("message") or "").strip(), MESSAGE_CHARS),
        feedback=feedback,
        pr_url=str(task.get("pr_url") or ""),
        spec_path=str(task.get("spec_path") or ""),
        waiting=waiting_behind(queue, str(task["task_id"])),
        fixes=_fixes(task),
        max_fixes=max_fixes,
    )


def _dormant(task: Mapping[str, Any], project: str, queue: Sequence[Mapping[str, Any]], due: float,
             stopped: Optional[float], now: float) -> Item:
    return Item(
        project=project,
        kind=REVIEW_DORMANT,
        event_at=due,
        task_id=str(task["task_id"]),
        stage=str(task.get("stage") or ""),
        pr_url=str(task.get("pr_url") or ""),
        spec_path=str(task.get("spec_path") or ""),
        waiting=waiting_behind(queue, str(task["task_id"])),
        polling_stopped_at=stopped,
        reopen_until=now + REOPEN_SECONDS,
    )


def _last_error(task: Mapping[str, Any]) -> Mapping[str, Any]:
    error = task.get("last_error")
    return error if isinstance(error, Mapping) else {}


def _attempts(task: Mapping[str, Any]) -> str:
    attempts = task.get("attempts")
    if not isinstance(attempts, Mapping):
        return ""
    return ", ".join(f"{stage} {count}" for stage, count in sorted(attempts.items()) if _whole(count) and count)


def _fixes(task: Mapping[str, Any]) -> int:
    record = task.get("planner_fixes")
    count = record.get("count") if isinstance(record, Mapping) else None
    return count if _whole(count) and count > 0 else 0


def _feedback(task: Mapping[str, Any]) -> Tuple[str, ...]:
    raw = task.get("review_feedback")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return ()
    comments = [" ".join(str(comment).split()) for comment in raw]
    comments = [comment for comment in comments if comment]
    kept = [_clip(comment, FEEDBACK_CHARS) for comment in comments[:FEEDBACK_ITEMS]]
    if len(comments) > FEEDBACK_ITEMS:
        kept.append(f"... and {len(comments) - FEEDBACK_ITEMS} more")
    return tuple(kept)


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"
