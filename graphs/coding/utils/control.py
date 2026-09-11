"""Operator control plane: status, retry, reset, skip, abort.

The manifest is the API. Every operation here is a manifest write, which means
hand-editing the JSON and running a tick does exactly what these do — these just
make it hard to get wrong.

Two properties are deliberate:

- **Nothing here runs the pipeline.** `retry` does not execute a task; it makes
  the task runnable and lets the next tick pick it up. There is one execution
  path, and it is the tick.
- **`reset` is the only destructive one**, and it says so: it closes the PR,
  deletes the branch on both sides and removes the worktree. Everything else
  only moves a status.
"""
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from graphs.coding.utils import manifest as manifest_store
from graphs.coding.utils.repo import (
    get_push_identity,
    get_repo_descriptor,
    project_root,
    resolve_repo_root,
)
from core.util import git_ops

# What a reset wipes. Keeping the list in one place is what stops a reset from
# leaving a digest behind that would make the next tick skip the implement.
RESET_FIELDS: Dict[str, Any] = {
    "status": "queued",
    "stage": "queued",
    "attempts": {},
    "impl_digest": None,
    "verified_digest": None,
    "head_sha": None,
    "review_cursor": None,
    "pr_url": None,
    "pr_number": None,
    "commit_url": None,
    "poll_until": None,
    "last_error": None,
    "lease_owner": None,
    "lease_expires_at": None,
}

STATUS_ICONS = {
    "queued": "⏳",
    "pending": "⏳",
    "active": "🔄",
    "awaiting_review": "👀",
    "done": "✅",
    "failed": "❌",
    "halted": "⚠️",
    "blocked": "⏭️",
}


class ControlError(Exception):
    """A control command that cannot be carried out, with a reason for the operator."""


def _require_task(manifest: Dict[str, Any], task_id: str) -> Dict[str, Any]:
    task = manifest_store.find_task(manifest, task_id)
    if task is None:
        known = ", ".join(t.get("task_id", "?") for t in (manifest.get("queue") or [])) or "none"
        raise ControlError(f"No task `{task_id}` in the manifest. Known tasks: {known}.")
    return task


def status_report(manifest_path: str, task_id: Optional[str] = None) -> str:
    """A plain-text view of the queue, or of one task in detail."""
    manifest = manifest_store.load_manifest(manifest_path)
    queue = manifest.get("queue") or []

    if task_id:
        return _task_detail(_require_task(manifest, task_id))

    if not queue:
        return "The queue is empty."

    lines = [f"**{manifest.get('project_name') or 'coding'}** — {len(queue)} task(s)"]
    now = time.time()
    for task in queue:
        lines.append(_task_summary(task, now))
    return "\n".join(lines)


def _task_summary(task: Dict[str, Any], now: float) -> str:
    status = task.get("status") or "queued"
    icon = STATUS_ICONS.get(status, "•")
    parts = [f"{icon} `{task.get('task_id')}` {status}"]

    stage = task.get("stage")
    if stage and stage not in ("queued", status):
        parts.append(f"stage {stage}")
    if manifest_store.lease_is_active(task, now):
        remaining = int(float(task.get("lease_expires_at") or now) - now)
        parts.append(f"claimed by {task.get('lease_owner')} ({remaining}s left)")
    if task.get("pr_url"):
        parts.append(str(task["pr_url"]))

    attempts = {k: v for k, v in (task.get("attempts") or {}).items() if v}
    if attempts:
        parts.append("attempts " + ", ".join(f"{k}×{v}" for k, v in sorted(attempts.items())))

    error = task.get("last_error") or {}
    if error.get("message"):
        parts.append(f"— {str(error['message'])[:120]}")

    return " · ".join(parts)


def _task_detail(task: Dict[str, Any]) -> str:
    lines = [
        f"### `{task.get('task_id')}`",
        f"- **Status**: {task.get('status')} (stage `{task.get('stage') or 'queued'}`)",
        f"- **Branch**: `{task.get('branch_name') or '(none)'}`",
        f"- **PR**: {task.get('pr_url') or '(none)'}",
        f"- **Attempts**: {task.get('attempts') or '{}'}",
        f"- **Dependencies**: {', '.join(task.get('dependencies') or []) or '(none)'}",
    ]

    if manifest_store.lease_is_active(task):
        lines.append(f"- **Lease**: held by `{task.get('lease_owner')}`")

    poll_until = task.get("poll_until")
    if poll_until:
        remaining = int(float(poll_until) - time.time())
        lines.append(
            f"- **Review polling**: {'active, ' + str(remaining) + 's left' if remaining > 0 else 'window closed'}"
        )

    error = task.get("last_error") or {}
    if error:
        lines.append(f"- **Last error** ({error.get('kind')} at `{error.get('stage')}`): {error.get('message')}")

    return "\n".join(lines)


def retry(manifest_path: str, task_id: str, from_stage: Optional[str] = None) -> str:
    """Makes a halted or failed task runnable again.

    This does not re-run anything. It clears the block and the next tick resumes
    from the recorded stage — so a task that already has good code and a passing
    test costs nothing to retry.
    """
    manifest = manifest_store.load_manifest(manifest_path)
    task = _require_task(manifest, task_id)

    if task.get("status") == "done":
        raise ControlError(f"`{task_id}` is already done. Use `reset` to run it again.")

    fields: Dict[str, Any] = {"last_error": None}
    if from_stage:
        if from_stage not in manifest_store.STAGE_ORDER:
            raise ControlError(
                f"Unknown stage `{from_stage}`. Valid stages: {', '.join(manifest_store.STAGE_ORDER)}."
            )
        # Rewinding the stage without clearing the digest would let the guards
        # skip straight past the work you are asking to redo.
        fields["stage"] = from_stage
        fields["impl_digest"] = None
        fields["verified_digest"] = None

    # The attempt counters are what halted it; leaving them would halt it again
    # on the very next tick.
    fields["attempts"] = {}

    manifest_store.yield_task(manifest_path, task_id, **fields)
    stage = fields.get("stage") or task.get("stage") or "queued"
    return f"`{task_id}` is queued again; the next tick resumes at stage `{stage}`."


async def reset(manifest_path: str, task_id: str, dry_run: bool = False) -> str:
    """Puts a task back to the start, destroying the work it produced.

    Closes the PR, deletes the branch locally and on the remote, removes the
    worktree, and clears every recorded digest and counter. Anything less leaves
    a trace that makes the next run behave differently from a first run — a
    stale digest skips the implement, a live branch makes the push a no-op.
    """
    manifest = manifest_store.load_manifest(manifest_path)
    task = _require_task(manifest, task_id)

    descriptor = get_repo_descriptor(manifest)
    # The same root the scheduler provisioned into. Resolving from the cwd
    # instead made a reset run from outside the project root quietly clean up
    # nothing and still report success.
    repo_root = resolve_repo_root(descriptor) or project_root()

    branch = task.get("branch_name") or ""
    pr_url = task.get("pr_url") or ""
    run_id = task.get("run_id") or ""
    workspace_path = os.path.join(repo_root, "workspaces", "runs", run_id) if run_id else ""

    planned = [f"clear the state of `{task_id}`"]
    if pr_url:
        planned.append(f"close {pr_url}")
    if branch:
        planned.append(f"delete branch `{branch}` (local and remote)")
    if workspace_path:
        planned.append(f"remove the worktree at `{workspace_path}`")

    if dry_run:
        return "Reset would: " + "; ".join(planned) + "."

    push_identity = get_push_identity(manifest)
    target_repo = descriptor.get("slug")
    cwd = workspace_path if workspace_path and os.path.exists(workspace_path) else repo_root

    notes: List[str] = []

    if pr_url:
        ok, message = await git_ops.close_pull_request(
            workspace_path=cwd, pr_url_or_number=pr_url,
            target_repo=target_repo, push_identity=push_identity
        )
        notes.append("closed the PR" if ok else f"could not close the PR ({message})")

    if workspace_path:
        ok, message = await git_ops.teardown_worktree(repo_root, workspace_path)
        notes.append("removed the worktree" if ok else f"could not remove the worktree ({message})")

    if branch:
        ok, message = await git_ops.delete_branch(
            repo_path=repo_root, branch_name=branch, target_repo=target_repo,
            push_identity=push_identity
        )
        notes.append("deleted the branch" if ok else f"could not delete the branch ({message})")

    # The manifest is cleared last and unconditionally: a task left pointing at
    # a PR that is already closed is worse than one whose cleanup half-failed.
    manifest_store.persist_task(manifest_path, task_id, run_id=None, branch_name=None, **RESET_FIELDS)

    detail = ("; ".join(notes) + ". ") if notes else ""
    return f"`{task_id}` reset. {detail}It will start from scratch on the next tick."


def skip(manifest_path: str, task_id: str) -> str:
    """Takes a task out of the queue without failing it.

    `blocked` is not runnable, so dependents stay blocked too — which is the
    honest outcome: they were waiting on work that is not going to happen.
    """
    manifest = manifest_store.load_manifest(manifest_path)
    task = _require_task(manifest, task_id)

    if task.get("status") == "done":
        raise ControlError(f"`{task_id}` is already done; there is nothing to skip.")

    manifest_store.persist_task(
        manifest_path, task_id,
        status="blocked", lease_owner=None, lease_expires_at=None
    )
    dependents = [t.get("task_id") for t in (manifest.get("queue") or [])
                  if task_id in (t.get("dependencies") or [])]
    suffix = f" Dependents now blocked: {', '.join(dependents)}." if dependents else ""
    return f"`{task_id}` skipped.{suffix}"


def abort(manifest_path: str, task_id: str, reason: str = "") -> str:
    """Stops a task now and marks it failed, leaving the evidence in place.

    The branch, worktree and PR are untouched on purpose: abort is what you run
    when something is wrong and you want to look at it. `reset` is what you run
    when you have finished looking.
    """
    manifest = manifest_store.load_manifest(manifest_path)
    _require_task(manifest, task_id)

    manifest_store.persist_task(
        manifest_path, task_id,
        status="failed",
        last_error={
            "stage": "control", "kind": "operator",
            "message": reason or "Aborted by the operator.", "at": time.time()
        },
        lease_owner=None, lease_expires_at=None
    )
    return f"`{task_id}` aborted. The branch, worktree and PR are left in place for inspection."


def unblock(manifest_path: str, task_id: str) -> str:
    """Returns a skipped task to the queue."""
    manifest = manifest_store.load_manifest(manifest_path)
    task = _require_task(manifest, task_id)

    if task.get("status") != "blocked":
        raise ControlError(f"`{task_id}` is `{task.get('status')}`, not blocked.")

    manifest_store.yield_task(manifest_path, task_id)
    return f"`{task_id}` is queued again."
