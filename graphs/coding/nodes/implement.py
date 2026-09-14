"""implement — the only node that calls the LLM to write code.

It touches nothing outside the worktree: no git, no network, no manifest beyond
its own bookkeeping. That separation is the point. When a push or a PR call
fails, the next tick resumes at `publish` and this node is skipped entirely,
because its output — the code in the worktree — is still there and its digest
still matches.
"""
import os
import time
from typing import Any, Dict, List

from graphs.coding.prompts.coder_prompt import build_coder_prompt
from graphs.coding.schemas import CodingState
from graphs.coding.utils import manifest as manifest_store
from graphs.coding.utils.dag import resolve_manifest_path
from graphs.coding.utils.digest import compute_worktree_digest, digest_matches
from graphs.coding.utils.token_opt import sanitize_traceback
from graphs.coding.utils.worker import call_worker
from graphs.coding.utils.xml_parsers import parse_worker_handoff_xml
from core.util import git_ops

MAX_IMPLEMENT_ATTEMPTS = 3

# A worker saying "I was not allowed to" is not a worker that failed to think.
# Three more attempts cannot grant it a tool, so these halt instead of retrying.
_BLOCKED_MARKERS = (
    "does not have permission",
    "permission restriction",
    "not have filesystem permission",
    "no permission",
)


def _is_blocked(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _BLOCKED_MARKERS)


async def implement_node(state: CodingState) -> Dict[str, Any]:
    manifest_path = resolve_manifest_path(state.get("build_request_path"))
    current_task = state.get("current_task") or {}
    task_id = current_task.get("task_id")
    workspace_path = state.get("workspace_path", "")
    report = list(state.get("tick_report") or [])

    if not task_id:
        return {"error_message": "implement: current_task has no task_id.", "route": "done"}

    # Guard: the work is already here. `digest_matches` is deliberately strict —
    # a digest that cannot be computed (worktree gone) is not a match, so the
    # code gets rewritten rather than silently assumed present.
    if manifest_store.stage_at_or_past(current_task, "implemented"):
        if await digest_matches(workspace_path, current_task.get("impl_digest")):
            report.append(f"⏩ `{task_id}` already implemented (digest unchanged); skipping the LLM.")
            return {"stage": "implemented", "route": "verify", "tick_report": report, "error_message": ""}

    attempts = int((current_task.get("attempts") or {}).get("implement", 0))
    if attempts >= MAX_IMPLEMENT_ATTEMPTS:
        message = (
            f"`{task_id}` exhausted its implement budget "
            f"({attempts}/{MAX_IMPLEMENT_ATTEMPTS}). Left for a human."
        )
        manifest_store.persist_task(
            manifest_path, task_id,
            status="halted",
            last_error={"stage": "implement", "kind": "llm", "message": message, "at": time.time()},
            lease_owner=None, lease_expires_at=None
        )
        report.append(f"⚠️ {message}")
        return {"route": "done", "tick_report": report, "error_message": message}

    spec_path = current_task.get("spec_path", "")
    spec_content = _read_spec(state.get("spec_path") or spec_path)

    prompt = build_coder_prompt(
        workspace_path=workspace_path,
        task_id=task_id,
        spec_path=spec_path,
        allowed_files=current_task.get("allowed_files", []),
        acceptance_criteria=current_task.get("acceptance_criteria", ""),
        verification_command=current_task.get("verification_command", ""),
        spec_content=spec_content,
        test_stderr=_clean(state.get("test_stderr")),
        critic_feedback=state.get("audit_feedback"),
        human_feedback=_review_feedback(state)
    )

    # The count has to travel in state, not just to disk. `current_task` is the
    # snapshot the scheduler took at the start of the tick, and verify reads the
    # budget off it: leaving it stale meant verify saw 0 attempts however many
    # times it ran, so implement→verify→implement never terminated and one tick
    # burned the whole recursion limit on LLM calls. Every write below therefore
    # feeds its result back into `current_task`, so state and manifest agree.
    attempt_count = manifest_store.bump_attempt(manifest_path, task_id, "implement")
    current_task = {
        **current_task,
        "attempts": {**(current_task.get("attempts") or {}), "implement": attempt_count},
    }

    summary = ""
    status = ""
    agent_error = ""
    try:
        reply = await call_worker(
            prompt=prompt,
            graph_id=state.get("graph_id") or "coding",
            channel=state.get("channel") or "coding-pipeline"
        )
        handoff = parse_worker_handoff_xml(reply)
        summary = handoff.get("implementation_summary", "")
        status = (handoff.get("status") or "").strip().upper()
    except Exception as e:
        agent_error = str(e)
        print(f"implement: agent_call error: {e}")

    # git is the ground truth for what changed; the model's own file list is not
    # (§7.6), so the XML file list is ignored entirely.
    modified_files = await _modified_files(workspace_path)
    digest = await compute_worktree_digest(workspace_path)

    if not modified_files:
        # The worker's own account of why. Throwing it away is how "Agent
        # graph-worker does not have permission to perform 'ls' on
        # .../run_21B6" reached the channel as "Worker completed without
        # modifying any file" — a sentence that reads like a lazy model and
        # sent three tasks through their whole budget before anyone looked.
        reason = summary.strip() if status == "FAILED" and summary.strip() else ""
        message = (
            f"Worker completed without modifying any file in {workspace_path}"
            + (f": {reason}" if reason else "")
            + (f" ({agent_error})" if agent_error else "")
        )

        blocked = _is_blocked(reason) or _is_blocked(agent_error)
        if blocked:
            # No model can grant itself a tool. Retrying costs three LLM calls
            # and ends in the same place, so stop and say what is actually wrong.
            stored = manifest_store.persist_task(
                manifest_path, task_id,
                status="halted",
                last_error={"stage": "implement", "kind": "config",
                            "message": message, "at": time.time()},
                lease_owner=None, lease_expires_at=None
            )
            report.append(f"⚠️ `{task_id}`: {message}")
            return {
                "current_task": stored or current_task,
                "implementation_summary": summary or message,
                "modified_files": [],
                "route": "done",
                "tick_report": report,
                "error_message": message
            }

        stored = manifest_store.yield_task(
            manifest_path, task_id,
            last_error={"stage": "implement", "kind": "llm", "message": message, "at": time.time()}
        )
        report.append(f"⚠️ `{task_id}`: {message}")
        # No stage advance: the next tick retries implement until the budget runs out.
        return {
            "current_task": stored or current_task,
            "implementation_summary": summary or message,
            "modified_files": [],
            "route": "done",
            "tick_report": report,
            "error_message": message
        }

    stored = manifest_store.persist_task(
        manifest_path, task_id,
        stage="implemented",
        impl_digest=digest,
        last_error=None
    )
    report.append(f"🧠 `{task_id}`: implemented ({len(modified_files)} file(s) changed).")

    return {
        "current_task": stored or current_task,
        "stage": "implemented",
        "route": "verify",
        "impl_digest": digest,
        "modified_files": modified_files,
        "implementation_summary": summary or f"Modified {len(modified_files)} file(s).",
        # Consumed: the next implement must not be re-prompted with stale feedback.
        "test_stderr": "",
        "audit_feedback": "",
        "latest_human_feedback": "",
        "github_pr_comments": [],
        "tick_report": report,
        "error_message": ""
    }


def _read_spec(path: str) -> str:
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _clean(stderr: Any) -> Any:
    return sanitize_traceback(stderr) if stderr else None


def _review_feedback(state: CodingState) -> Any:
    comments: List[str] = state.get("github_pr_comments") or []
    if comments:
        return "GitHub PR review comments:\n" + "\n".join(comments)
    return state.get("latest_human_feedback")


async def _modified_files(workspace_path: str) -> List[str]:
    if not workspace_path or not os.path.exists(workspace_path):
        return []
    code, out, _ = await git_ops.run_cmd_async(
        ["git", "status", "--porcelain"], cwd=workspace_path, timeout=10.0
    )
    if code != 0 or not out.strip():
        return []
    files = []
    for line in out.strip().splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2:
            files.append(parts[1])
    return files
