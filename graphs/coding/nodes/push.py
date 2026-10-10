"""push — commits the worker's change and pushes it to the task branch.

Runs straight after implement, before anything tests the code. Two reasons the
push moved here from publish:

- **The remote branch is the source of truth.** Verification fetches the
  commit from GitHub (the sandbox never sees this host's disk), and a tick on
  another host resumes from `origin/<branch>`. Neither works for code that only
  exists in a local worktree.
- **Progress is tracked by commit SHA.** `head_sha` names exactly what verify
  and audit looked at, so their guards compare SHAs instead of digesting a tree
  that may not exist on this host.

Every attempt is a commit on the task branch, never on the default branch. A
failed attempt stays in the branch history, and the PR squash-merges it away.

No LLM call here, so a push that fails because GitHub is down is retried on the
next tick without spending tokens.
"""
import os
import time
from typing import Any, Dict

from graphs.coding.schemas import CodingState
from graphs.coding.utils import manifest as manifest_store
from graphs.coding.utils.dag import resolve_manifest_path
from graphs.coding.utils.repo import get_push_identity, get_repo_descriptor
from core.util import git_ops

MAX_PUSH_ATTEMPTS = 3


async def push_node(state: CodingState) -> Dict[str, Any]:
    manifest_path = resolve_manifest_path(state.get("build_request_path"))
    current_task = state.get("current_task") or {}
    task_id = current_task.get("task_id")
    workspace_path = state.get("workspace_path") or ""
    branch_name = state.get("branch_name") or current_task.get("branch_name") or ""
    report = list(state.get("tick_report") or [])

    if not task_id:
        return {"error_message": "push: current_task has no task_id.", "route": "done"}

    # Guard: this attempt is already on the remote.
    head_sha = current_task.get("head_sha") or ""
    if manifest_store.stage_at_or_past(current_task, "pushed") and head_sha:
        return {"stage": "pushed", "head_sha": head_sha, "route": "verify",
                "tick_report": report, "error_message": ""}

    repo_descriptor = get_repo_descriptor(state)
    default_branch = repo_descriptor.get("default_branch") or "main"

    # Attempts are allowed on the remote only because they are contained in a
    # task branch. A misconfigured branch name must never push to the default.
    # `commit_and_push` refuses too; checking here makes it a config halt
    # instead of three retries of a push that can never succeed.
    refusal = git_ops.push_refusal(branch_name, default_branch)
    if refusal:
        message = (
            f"`{task_id}`: refusing to push ({refusal}); "
            f"attempts must go to a `feat/` task branch, never to `{default_branch}`."
        )
        manifest_store.persist_task(
            manifest_path, task_id,
            status="halted",
            last_error={"stage": "push", "kind": "config", "message": message, "at": time.time()},
            lease_owner=None, lease_expires_at=None
        )
        report.append(f"⚠️ {message}")
        return {"route": "done", "tick_report": report, "error_message": message}

    if not workspace_path or not os.path.exists(workspace_path):
        # The change was never committed, so it is gone with the worktree. The
        # scheduler notices the missing worktree and sends it back to implement.
        message = f"`{task_id}`: workspace {workspace_path or '(none)'} does not exist; nothing to push."
        manifest_store.yield_task(
            manifest_path, task_id,
            last_error={"stage": "push", "kind": "git", "message": message, "at": time.time()}
        )
        report.append(f"⚠️ {message}")
        return {"route": "done", "tick_report": report, "error_message": message}

    attempt = int((current_task.get("attempts") or {}).get("implement", 0)) or 1
    push_identity = get_push_identity(state)
    commit_msg = (
        f"feat({state.get('project_name') or 'coding'}): {task_id} (attempt {attempt})"
    )
    push_ok, push_log = await git_ops.commit_and_push(
        workspace_path=workspace_path,
        branch_name=branch_name,
        commit_msg=commit_msg,
        author=push_identity.author if push_identity else None,
        push_identity=push_identity,
        default_branch=default_branch
    )
    new_sha = await git_ops.rev_parse(workspace_path) if push_ok else ""

    if not push_ok or not new_sha:
        message = (
            f"Push failed for `{task_id}`: {push_log or 'could not resolve HEAD'} "
            f"The change is committed locally on `{branch_name}` at `{workspace_path}`."
        )
        attempts = manifest_store.bump_attempt(manifest_path, task_id, "push")
        error = {"stage": "push", "kind": "git", "message": message, "at": time.time()}
        if attempts < MAX_PUSH_ATTEMPTS:
            # Stage stays `implemented`: the next tick re-enters here, and
            # `commit_and_push` treats "nothing to commit" as success.
            manifest_store.yield_task(manifest_path, task_id, last_error=error)
            report.append(f"↻ {message} Retrying on the next tick ({attempts}/{MAX_PUSH_ATTEMPTS}).")
        else:
            manifest_store.persist_task(
                manifest_path, task_id,
                status="halted", last_error=error,
                lease_owner=None, lease_expires_at=None
            )
            report.append(f"⚠️ {message} Halted after {attempts} attempts — reply `retry {task_id}` when fixed.")
        return {"route": "done", "tick_report": report, "error_message": message}

    stored = manifest_store.persist_task(
        manifest_path, task_id,
        stage="pushed", head_sha=new_sha, last_error=None
    )
    report.append(f"📤 `{task_id}`: pushed `{new_sha[:7]}` to `{branch_name}`.")
    return {
        "current_task": stored or {**current_task, "stage": "pushed", "head_sha": new_sha},
        "stage": "pushed",
        "head_sha": new_sha,
        "route": "verify",
        "tick_report": report,
        "error_message": ""
    }
