"""sync_review — reads the decision from GitHub and acts on it.

GitHub is the only approval surface. There is no interrupt, no chat approval and
no keyword classification: a decision is a state on the PR, evaluated by an exact
rule. That is the direct fix for the gate that read "ok" inside "looks broken"
and defaulted to *approved* when it found nothing (E1, E2).

Signals, first match wins:

| # | Approval          | What it is                                   |
|---|-------------------|----------------------------------------------|
| 1 | merged_manually   | PR state is MERGED — terminal, checked first  |
| 2 | review_approved   | a native review by a listed reviewer          |
| 3 | label:approved    | the `approved` label                          |
| 4 | comment:/approve  | a comment whose *first line* is exactly /approve |

Rejections are symmetric: CHANGES_REQUESTED, `/reject` (back to implement) and
`/abort` (fail the task and tear down).
"""
import time
from typing import Any, Dict, List, Optional, Tuple

from graphs.coding.schemas import CodingState
from graphs.coding.utils import manifest as manifest_store
from graphs.coding.utils.dag import resolve_manifest_path
from graphs.coding.utils.repo import get_push_identity, get_repo_descriptor
from core.util import git_ops

DEFAULT_APPROVAL_SIGNALS = ["merged_manually", "review_approved", "label:approved", "comment:/approve"]
DEFAULT_REJECTION_SIGNALS = ["changes_requested", "comment:/reject", "comment:/abort"]

APPROVED_LABEL = "approved"


def first_line_command(body: str) -> str:
    """The slash command on a comment's first line, or "".

    Exact match on the first line only. Prose cannot trigger it, a quoted
    `/approve` cannot trigger it, and a code block mentioning it cannot either —
    the substring matching that made "ok" an approval is gone.
    """
    if not body:
        return ""
    first = body.strip().splitlines()[0].strip().lower() if body.strip() else ""
    return first if first in ("/approve", "/reject", "/abort") else ""


def _author_of(entry: Dict[str, Any]) -> str:
    author = entry.get("author")
    if isinstance(author, dict):
        return (author.get("login") or "").lower()
    return str(author or "").lower()


def evaluate_signals(
    pr_status: Dict[str, Any],
    reviewers: List[str],
    approval_signals: Optional[List[str]] = None,
    rejection_signals: Optional[List[str]] = None
) -> Tuple[str, str]:
    """Maps a PR's state to (decision, evidence).

    decision ∈ {"merged", "approved", "changes_requested", "abort", "pending"}.
    """
    approval_signals = approval_signals or DEFAULT_APPROVAL_SIGNALS
    rejection_signals = rejection_signals or DEFAULT_REJECTION_SIGNALS
    allowed = {r.lower() for r in (reviewers or [])}

    state = (pr_status.get("state") or "").upper()
    if state == "MERGED" and "merged_manually" in approval_signals:
        return "merged", "PR was merged on GitHub."
    if state == "CLOSED":
        return "abort", "PR was closed without merging."

    comments = pr_status.get("comments") or []

    # Rejections are evaluated before approvals: if you asked for changes and
    # then someone added a label, the changes still win.
    decision = (pr_status.get("reviewDecision") or "").upper()
    if decision == "CHANGES_REQUESTED" and "changes_requested" in rejection_signals:
        return "changes_requested", "Reviewer requested changes."

    for comment in comments:
        author = _author_of(comment)
        if allowed and author not in allowed:
            continue
        command = first_line_command(comment.get("body", ""))
        if command == "/abort" and "comment:/abort" in rejection_signals:
            return "abort", f"`/abort` from @{author}."
        if command == "/reject" and "comment:/reject" in rejection_signals:
            return "changes_requested", f"`/reject` from @{author}."

    if "review_approved" in approval_signals and decision == "APPROVED":
        approvers = [
            _author_of(r) for r in (pr_status.get("latestReviews") or [])
            if (r.get("state") or "").upper() == "APPROVED"
        ]
        if not allowed or any(a in allowed for a in approvers):
            return "approved", "Approved via GitHub review."

    if "label:approved" in approval_signals:
        labels = {str((l.get("name") if isinstance(l, dict) else l) or "").lower()
                  for l in (pr_status.get("labels") or [])}
        if APPROVED_LABEL in labels:
            return "approved", "The `approved` label is set."

    if "comment:/approve" in approval_signals:
        for comment in comments:
            author = _author_of(comment)
            if allowed and author not in allowed:
                continue
            if first_line_command(comment.get("body", "")) == "/approve":
                return "approved", f"`/approve` from @{author}."

    return "pending", ""


def harvest_comments(
    pr_status: Dict[str, Any],
    reviewers: List[str],
    review_cursor: Optional[str],
    bot_login: Optional[str]
) -> Tuple[List[str], Optional[str]]:
    """Collects new reviewer comments, returning (bodies, new_cursor).

    Filtered to the people who review, excluding the machine user — otherwise the
    system reacts to its own test-result comments — and to ids beyond the cursor,
    so a resume does not re-action a comment already addressed (E5).
    """
    allowed = {r.lower() for r in (reviewers or [])}
    bot = (bot_login or "").lower()
    cursor = int(review_cursor) if str(review_cursor or "").isdigit() else 0
    highest = cursor
    bodies: List[str] = []

    for comment in pr_status.get("comments") or []:
        author = _author_of(comment)
        if bot and author == bot:
            continue
        if allowed and author not in allowed:
            continue

        raw_id = comment.get("databaseId") or comment.get("id") or 0
        comment_id = int(raw_id) if str(raw_id).isdigit() else 0
        if comment_id and comment_id <= cursor:
            continue

        body = (comment.get("body") or "").strip()
        if not body or first_line_command(body):
            continue

        bodies.append(f"@{author}: {body}" if author else body)
        highest = max(highest, comment_id)

    return bodies, (str(highest) if highest else review_cursor)


async def sync_review_node(state: CodingState) -> Dict[str, Any]:
    manifest_path = resolve_manifest_path(state.get("build_request_path"))
    current_task = state.get("current_task") or {}
    task_id = current_task.get("task_id")
    workspace_path = state.get("workspace_path") or "."
    report = list(state.get("tick_report") or [])

    if not task_id:
        return {"error_message": "sync_review: current_task has no task_id.", "route": "done"}

    pr_url = state.get("pr_url") or current_task.get("pr_url") or ""
    if not pr_url:
        # Nothing to sync against: fall back to publishing, which adopts an
        # existing PR for the branch if there is one.
        return {"route": "publish", "tick_report": report, "error_message": ""}

    repo_descriptor = get_repo_descriptor(state)
    target_repo = repo_descriptor.get("slug") or state.get("target_repo")
    push_identity = get_push_identity(state)
    reviewers = state.get("reviewers") or []

    pr_status = await git_ops.get_pull_request_status(
        workspace_path, pr_url, target_repo=target_repo, push_identity=push_identity
    )

    decision, evidence = evaluate_signals(
        pr_status,
        reviewers=reviewers,
        approval_signals=state.get("approval_signals"),
        rejection_signals=state.get("rejection_signals")
    )

    if decision in ("approved", "merged"):
        return await _finish(
            manifest_path, state, current_task, pr_status, pr_url, target_repo,
            push_identity, decision, evidence, report
        )

    if decision == "abort":
        manifest_store.persist_task(
            manifest_path, task_id,
            status="failed", stage="awaiting_review",
            last_error={"stage": "sync_review", "kind": "github",
                        "message": evidence, "at": time.time()},
            lease_owner=None, lease_expires_at=None
        )
        await _teardown(state)
        report.append(f"🛑 `{task_id}`: {evidence} Task marked failed and worktree removed.")
        return {"route": "scheduler", "tick_report": report, "error_message": ""}

    comments, new_cursor = harvest_comments(
        pr_status,
        reviewers=reviewers,
        review_cursor=current_task.get("review_cursor"),
        bot_login=push_identity.login if push_identity else None
    )

    if decision == "changes_requested" or comments:
        manifest_store.persist_task(
            manifest_path, task_id,
            status="active", stage="provisioned",
            impl_digest=None, review_cursor=new_cursor,
            poll_until=None, last_error=None
        )
        summary = evidence or f"{len(comments)} new review comment(s)."
        report.append(f"🔁 `{task_id}`: {summary} Sending it back to the worker.")
        return {
            "stage": "provisioned",
            "github_pr_comments": comments,
            "route": "implement",
            "tick_report": report,
            "error_message": ""
        }

    # Nothing new. Release the claim so the next tick can look again.
    manifest_store.release_lease(manifest_path, task_id, owner=state.get("lease_owner"))
    if new_cursor and new_cursor != current_task.get("review_cursor"):
        manifest_store.persist_task(manifest_path, task_id, review_cursor=new_cursor)
    return {"route": "scheduler", "tick_report": report, "error_message": ""}


async def _finish(
    manifest_path: str,
    state: CodingState,
    current_task: Dict[str, Any],
    pr_status: Dict[str, Any],
    pr_url: str,
    target_repo: Optional[str],
    push_identity,
    decision: str,
    evidence: str,
    report: List[str]
) -> Dict[str, Any]:
    """Merges if needed, tears down, and marks the task done."""
    task_id = current_task["task_id"]
    commit_url = ""

    if decision == "merged":
        commit_url = _merge_commit_url(pr_status, pr_url)
    else:
        merge_ok, merge_commit, merge_log = await git_ops.merge_pull_request(
            workspace_path=state.get("workspace_path") or ".",
            pr_url_or_number=pr_url,
            squash=True,
            delete_branch=True,
            target_repo=target_repo,
            push_identity=push_identity
        )
        if not merge_ok:
            # The approval stands; only the merge failed. Stay in awaiting_review
            # so the next tick retries instead of marking a task done that is not.
            message = f"`{task_id}` is approved but the merge failed: {merge_log}"
            manifest_store.persist_task(
                manifest_path, task_id,
                last_error={"stage": "merge", "kind": "github", "message": merge_log, "at": time.time()},
                poll_until=time.time() + 15 * 60,
                lease_owner=None, lease_expires_at=None
            )
            report.append(f"⚠️ {message}")
            return {"route": "scheduler", "tick_report": report, "error_message": message}
        commit_url = merge_commit

    await _teardown(state)
    manifest_store.persist_task(
        manifest_path, task_id,
        status="done", stage="done",
        commit_url=commit_url, poll_until=None, last_error=None,
        lease_owner=None, lease_expires_at=None
    )
    report.append(f"🎉 `{task_id}` merged ({evidence}) → {commit_url or pr_url}")
    return {
        "stage": "done",
        "commit_url": commit_url,
        "route": "scheduler",
        "tick_report": report,
        "error_message": ""
    }


def _merge_commit_url(pr_status: Dict[str, Any], pr_url: str) -> str:
    merge_commit = pr_status.get("mergeCommit")
    sha = merge_commit.get("oid") if isinstance(merge_commit, dict) else None
    if sha and "/pull/" in pr_url:
        return f"{pr_url.split('/pull/')[0]}/commit/{sha}"
    return pr_url


async def _teardown(state: CodingState) -> None:
    workspace_path = state.get("workspace_path")
    if workspace_path:
        try:
            await git_ops.teardown_worktree(".", workspace_path)
        except Exception as e:
            print(f"sync_review: worktree teardown failed: {e}")
