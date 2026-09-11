"""verify — runs the task's verification command in the worktree.

The only blocking quality gate (§7). Two things it deliberately does not do:

- it does not require a PR to exist. Gating local tests behind a network
  operation is what turned a GitHub hiccup into "tests failed" (A3);
- it does not re-run for a tree it has already tested. The digest it passed on
  is recorded, so a resumed tick goes straight to publish.
"""
import asyncio
import os
import time
from typing import Any, Dict

from graphs.coding.schemas import CodingState
from graphs.coding.utils import manifest as manifest_store
from graphs.coding.utils.dag import resolve_manifest_path
from graphs.coding.utils.digest import compute_worktree_digest
from graphs.coding.utils.token_opt import sanitize_traceback

VERIFY_TIMEOUT_SECONDS = 120.0
MAX_IMPLEMENT_ATTEMPTS = 3


async def verify_node(state: CodingState) -> Dict[str, Any]:
    manifest_path = resolve_manifest_path(state.get("build_request_path"))
    current_task = state.get("current_task") or {}
    task_id = current_task.get("task_id")
    workspace_path = state.get("workspace_path") or ""
    report = list(state.get("tick_report") or [])

    if not task_id:
        return {"error_message": "verify: current_task has no task_id.", "route": "done"}

    digest = state.get("impl_digest") or await compute_worktree_digest(workspace_path)

    # Guard: this exact tree already passed.
    if (
        manifest_store.stage_at_or_past(current_task, "verified")
        and digest
        and current_task.get("verified_digest") == digest
    ):
        report.append(f"⏩ `{task_id}` already verified for this tree; skipping tests.")
        return {"stage": "verified", "route": "audit", "test_run_passed": True,
                "tick_report": report, "error_message": ""}

    verification_cmd = (current_task.get("verification_command") or "").strip()
    if not verification_cmd:
        # An authoring error, not a coding error: retrying the LLM three times
        # cannot fix a missing command (B4), so halt with the actual problem.
        message = (
            f"`{task_id}` has no `verification_command`. Add one to the manifest "
            f"(the task cannot be verified, so it will not be published)."
        )
        manifest_store.persist_task(
            manifest_path, task_id,
            status="halted",
            last_error={"stage": "verify", "kind": "config", "message": message, "at": time.time()},
            lease_owner=None, lease_expires_at=None
        )
        report.append(f"⚠️ {message}")
        return {"route": "done", "test_run_passed": False, "tick_report": report, "error_message": message}

    if not os.path.exists(workspace_path):
        message = f"`{task_id}`: workspace {workspace_path} does not exist."
        manifest_store.yield_task(
            manifest_path, task_id,
            stage="queued", impl_digest=None,
            last_error={"stage": "verify", "kind": "git", "message": message, "at": time.time()}
        )
        report.append(f"⚠️ {message} Re-provisioning on the next tick.")
        return {"route": "done", "test_run_passed": False, "tick_report": report, "error_message": message}

    exit_code, stdout, stderr = await _run(verification_cmd, workspace_path)
    passed = exit_code == 0

    if passed:
        manifest_store.persist_task(
            manifest_path, task_id,
            stage="verified", verified_digest=digest, last_error=None
        )
        report.append(f"✅ `{task_id}`: {verification_cmd} passed.")
        return {
            "stage": "verified",
            "route": "audit",
            "test_run_passed": True,
            "test_stdout": stdout,
            "test_stderr": "",
            "tick_report": report,
            "error_message": ""
        }

    clean_err = sanitize_traceback(stderr) if stderr else stdout
    attempts = int((current_task.get("attempts") or {}).get("implement", 0))
    budget_left = attempts < MAX_IMPLEMENT_ATTEMPTS

    # A failing test is the one failure class that *should* spend an LLM attempt.
    manifest_store.persist_task(
        manifest_path, task_id,
        stage="provisioned",
        impl_digest=None,
        last_error={
            "stage": "verify", "kind": "verification",
            "message": f"exit {exit_code}: {clean_err[:500]}", "at": time.time()
        },
        **({} if budget_left else {"status": "halted", "lease_owner": None, "lease_expires_at": None})
    )

    if budget_left:
        report.append(f"❌ `{task_id}`: tests failed (attempt {attempts}); handing the output back to the worker.")
        return {
            "stage": "provisioned",
            "test_run_passed": False,
            "test_stdout": stdout,
            "test_stderr": clean_err,
            "route": "implement",
            "tick_report": report,
            "error_message": ""
        }

    message = f"`{task_id}` still failing after {attempts} implement attempts. Halted for a human."
    report.append(f"⚠️ {message}")
    return {
        "test_run_passed": False,
        "test_stdout": stdout,
        "test_stderr": clean_err,
        "route": "done",
        "tick_report": report,
        "error_message": message
    }


async def _run(command: str, cwd: str):
    """Runs the verification command in the worktree, using the repo's own env."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy()
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=VERIFY_TIMEOUT_SECONDS
        )
        return (
            proc.returncode or 0,
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace")
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return 124, "", f"Verification timed out after {VERIFY_TIMEOUT_SECONDS:.0f}s: {command}"
    except Exception as e:
        return 1, "", f"Verification could not run: {e}"
