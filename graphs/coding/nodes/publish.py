"""publish — commit, push, PR upsert, and the review comments.

Everything here is deterministic and idempotent, and nothing here calls the LLM.
That is what makes retrying safe: if `gh` is down, the next tick re-enters this
node with the same code and tries again, spending no tokens.

Two rules from §6.3 hold throughout:

- **A URL is only reported if `gh` confirmed it.** Fabricated links (D2) are
  gone, so a link in Discord is proof the PR exists.
- **A push that succeeded is never thrown away.** If the PR call fails, the task
  halts with a compare URL you can click, and the next tick adopts the PR you
  open from it.
"""
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from graphs.coding.schemas import CodingState
from graphs.coding.utils import manifest as manifest_store
from graphs.coding.utils.dag import resolve_manifest_path
from graphs.coding.utils.repo import get_push_identity, get_repo_descriptor
from core.util import git_ops
from core.util.push_identity import subprocess_env

MAX_PUBLISH_ATTEMPTS = 3
# How long the scheduled tick keeps re-checking a PR before going dormant.
POLL_WINDOW_SECONDS = 60 * 60


async def publish_node(state: CodingState) -> Dict[str, Any]:
    manifest_path = resolve_manifest_path(state.get("build_request_path"))
    current_task = state.get("current_task") or {}
    task_id = current_task.get("task_id")
    workspace_path = state.get("workspace_path") or ""
    branch_name = state.get("branch_name") or current_task.get("branch_name") or ""
    report = list(state.get("tick_report") or [])

    if not task_id:
        return {"error_message": "publish: current_task has no task_id.", "route": "done"}
    if not workspace_path or not branch_name:
        message = f"`{task_id}`: nothing to publish (no worktree or branch)."
        report.append(f"⚠️ {message}")
        return {"route": "done", "tick_report": report, "error_message": message}

    repo_descriptor = get_repo_descriptor(state)
    target_repo = (
        repo_descriptor.get("slug")
        or state.get("target_repo")
        or current_task.get("target_repo")
        or await git_ops.discover_target_repo(workspace_path, ".")
    )
    push_identity = get_push_identity(state)
    default_branch = repo_descriptor.get("default_branch") or "main"
    base_branch = (state.get("base_branch") or default_branch).replace("origin/", "")

    # 1. Commit + push. Idempotent: "nothing to commit" is fine, and pushing an
    #    already-pushed branch is a no-op.
    commit_msg = (
        f"feat({state.get('project_name') or 'coding'}): implement {task_id} "
        f"({state.get('run_id') or 'run'})"
    )
    push_ok, push_log = await git_ops.commit_and_push(
        workspace_path=workspace_path,
        branch_name=branch_name,
        commit_msg=commit_msg,
        author=push_identity.author if push_identity else None,
        push_identity=push_identity
    )

    if not push_ok:
        return _retry_or_halt(
            manifest_path, task_id, report,
            kind="git", stage_label="push",
            message=(
                f"Push failed for `{task_id}`: {push_log} "
                f"Work is preserved on branch `{branch_name}` at `{workspace_path}`."
            )
        )

    head_sha = await _head_sha(workspace_path)

    # 2. PR upsert. Always look before creating, so a PR you opened by hand from
    #    the compare URL is adopted instead of duplicated.
    pr_url, pr_number, pr_error = await _upsert_pull_request(
        workspace_path=workspace_path,
        branch_name=branch_name,
        base_branch=base_branch,
        target_repo=target_repo,
        title=f"feat: {task_id}",
        body=_pr_body(state, current_task, push_identity),
        existing_pr=state.get("pr_url") or current_task.get("pr_url") or "",
        push_identity=push_identity
    )

    if not pr_url:
        compare_url = _compare_url(target_repo, branch_name)
        message = (
            f"Could not open the PR for `{task_id}` ({pr_error}). "
            f"The branch is pushed — open it here: {compare_url}"
            if compare_url else
            f"Could not open the PR for `{task_id}` ({pr_error}). The branch is pushed."
        )
        return _retry_or_halt(
            manifest_path, task_id, report,
            kind="github", stage_label="publish", message=message,
            extra={"head_sha": head_sha}
        )

    # 3. Post what the reviewer needs to see next to the code.
    await _post_review_context(
        state=state, workspace_path=workspace_path, pr_url=pr_url,
        target_repo=target_repo, push_identity=push_identity
    )

    poll_until = time.time() + POLL_WINDOW_SECONDS
    manifest_store.persist_task(
        manifest_path, task_id,
        status="awaiting_review",
        stage="awaiting_review",
        pr_url=pr_url,
        pr_number=pr_number,
        head_sha=head_sha,
        poll_until=poll_until,
        last_error=None,
        lease_owner=None,
        lease_expires_at=None
    )

    report.append(f"🚀 `{task_id}` is ready for review: {pr_url}")

    return {
        "stage": "awaiting_review",
        "pr_url": pr_url,
        "pr_number": pr_number,
        "head_sha": head_sha,
        "poll_until": poll_until,
        "route": "scheduler",
        "tick_report": report,
        "error_message": ""
    }


def _retry_or_halt(
    manifest_path: str,
    task_id: str,
    report: List[str],
    kind: str,
    stage_label: str,
    message: str,
    extra: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Publishing failures retry within their own budget, never the LLM's."""
    attempts = manifest_store.bump_attempt(manifest_path, task_id, "publish")
    fields: Dict[str, Any] = dict(extra or {})
    fields["last_error"] = {
        "stage": stage_label, "kind": kind, "message": message, "at": time.time()
    }

    if attempts < MAX_PUBLISH_ATTEMPTS:
        # Hand it back: holding the lease over a transient `gh` failure would
        # keep the task invisible until the lease expired.
        manifest_store.yield_task(manifest_path, task_id, **fields)
        report.append(f"↻ {message} Retrying on the next tick ({attempts}/{MAX_PUBLISH_ATTEMPTS}).")
        return {"route": "done", "tick_report": report, "error_message": message}

    fields.update({"status": "halted", "lease_owner": None, "lease_expires_at": None})
    manifest_store.persist_task(manifest_path, task_id, **fields)
    report.append(f"⚠️ {message} Halted after {attempts} attempts — reply `retry {task_id}` when fixed.")
    return {"route": "done", "tick_report": report, "error_message": message}


async def _upsert_pull_request(
    workspace_path: str,
    branch_name: str,
    base_branch: str,
    target_repo: Optional[str],
    title: str,
    body: str,
    existing_pr: str,
    push_identity
) -> Tuple[str, Optional[int], str]:
    """Returns (url, number, error). Adopts an existing PR for the branch first."""
    if existing_pr:
        status = await git_ops.get_pull_request_status(
            workspace_path, existing_pr, target_repo=target_repo, push_identity=push_identity
        )
        if status.get("url"):
            return status["url"], status.get("number"), ""

    found_url, found_number = await _find_open_pr(
        workspace_path, branch_name, target_repo, push_identity
    )
    if found_url:
        return found_url, found_number, ""

    ok, url_or_error, number = await git_ops.create_pull_request(
        workspace_path=workspace_path,
        branch_name=branch_name,
        title=title,
        body=body,
        base_branch=base_branch,
        target_repo=target_repo,
        push_identity=push_identity
    )
    if ok and url_or_error.startswith("http"):
        return url_or_error, number, ""
    return "", None, url_or_error or "gh pr create failed"


async def _find_open_pr(
    workspace_path: str,
    branch_name: str,
    target_repo: Optional[str],
    push_identity
) -> Tuple[str, Optional[int]]:
    import json

    cmd = ["gh", "pr", "list", "--head", branch_name, "--state", "open", "--json", "url,number"]
    if target_repo:
        cmd.extend(["--repo", target_repo])

    code, out, _ = await git_ops.run_cmd_async(
        cmd,
        cwd=workspace_path if os.path.exists(workspace_path) else ".",
        timeout=20.0,
        env=subprocess_env(push_identity) or None
    )
    if code != 0 or not out.strip():
        return "", None
    try:
        entries = json.loads(out)
    except Exception:
        return "", None
    if not entries:
        return "", None
    return entries[0].get("url", ""), entries[0].get("number")


async def _head_sha(workspace_path: str) -> str:
    code, out, _ = await git_ops.run_cmd_async(
        ["git", "rev-parse", "HEAD"], cwd=workspace_path, timeout=10.0
    )
    return out.strip() if code == 0 else ""


def _compare_url(target_repo: Optional[str], branch_name: str) -> str:
    """A link you can open a PR from by hand when `gh` will not."""
    if not target_repo:
        return ""
    return f"https://github.com/{target_repo}/compare/{branch_name}?expand=1"


def _pr_body(state: CodingState, current_task: Dict[str, Any], push_identity) -> str:
    summary = state.get("implementation_summary") or "(no summary provided)"
    spec_path = state.get("spec_path") or current_task.get("spec_path", "")
    author = push_identity.author if push_identity else "coding graph"
    return (
        f"Automated PR from the coding graph.\n\n"
        f"- **Task**: `{current_task.get('task_id')}`\n"
        f"- **Spec**: `{spec_path}`\n"
        f"- **Author**: `{author}`\n\n"
        f"### Implementation summary\n{summary}\n\n"
        f"---\n"
        f"Approve with GitHub's review, the `approved` label, or a comment whose "
        f"first line is `/approve`. Request changes with a review or `/reject`."
    )


async def _post_review_context(
    state: CodingState,
    workspace_path: str,
    pr_url: str,
    target_repo: Optional[str],
    push_identity
) -> None:
    """Posts the test result and any advisory audit finding onto the PR.

    Failures here are logged, never fatal: the PR exists and is reviewable, and
    losing a comment must not halt a task or trigger a retry that re-pushes.
    """
    blocks: List[str] = []

    if state.get("test_run_passed"):
        command = (state.get("current_task") or {}).get("verification_command", "")
        blocks.append(f"### ✅ Verification passed\n`{command}`")

    audit_feedback = state.get("audit_feedback")
    if audit_feedback:
        blocks.append(
            "### 🔎 Audit (advisory — not a blocker)\n"
            f"{audit_feedback}"
        )

    if not blocks:
        return

    try:
        await git_ops.comment_pull_request(
            workspace_path=workspace_path,
            pr_url=pr_url,
            body="\n\n".join(blocks),
            target_repo=target_repo,
            push_identity=push_identity
        )
    except Exception as e:
        print(f"publish: could not post the review context comment: {e}")
