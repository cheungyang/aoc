"""verify — runs the task's verification command in the worktree.

The only blocking quality gate (§7). Two things it deliberately does not do:

- it does not require a PR to exist. Gating local tests behind a network
  operation is what turned a GitHub hiccup into "tests failed" (A3);
- it does not re-run for a tree it has already tested. The digest it passed on
  is recorded, so a resumed tick goes straight to publish.

It also does not hand every red run back to the worker. A command that fails
because the toolchain is not installed fails identically no matter what the
code says, so retrying it spends the whole implement budget rewriting code that
was never wrong.
"""
import os
import re
import time
from typing import Any, Dict

from graphs.coding.schemas import CodingState
from graphs.coding.utils import manifest as manifest_store
from graphs.coding.utils.dag import resolve_manifest_path
from graphs.coding.utils.digest import compute_worktree_digest
from graphs.coding.utils.shell import run_in_worktree
from graphs.coding.utils.token_opt import sanitize_traceback

# Ten minutes. The old 120s assumed the command was only a test run, but a
# verification command now routinely installs its own dependencies first, and a
# cold `npm install` alone can outlast two minutes. A timeout is charged to the
# implement budget as though the code were at fault, so a budget too short does
# not just slow the pipeline down — it halts correct work.
VERIFY_TIMEOUT_SECONDS = 600.0
MAX_IMPLEMENT_ATTEMPTS = 3

# Shells report "I could not run this at all" out of band from anything the
# command itself might have said: 127 is not-found, 126 is found-but-not-executable.
_UNRUNNABLE_EXIT_CODES = (126, 127)

_UNRUNNABLE_MARKERS = (
    "command not found",
    "is not recognized as an internal or external command",
    "err_module_not_found",
    "npm err! code enoent",
)

# The two runners in use announce a missing import differently; both name it.
_MISSING_IMPORT = re.compile(
    r"""(?:cannot find module|failed to resolve import|could not resolve)\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
_MISSING_PY_MODULE = re.compile(r"""no module named\s+['"]([^'"]+)['"]""", re.IGNORECASE)


def classify_failure(exit_code: int, stdout: str, stderr: str, workspace_path: str = "") -> str:
    """Returns `"environment"` when no edit to the code could make this pass.

    Deliberately conservative — everything it cannot prove is an environment
    problem stays `"verification"` and goes back to the worker, because
    misreading a real test failure as a broken environment would halt a task
    that one more attempt would have fixed.
    """
    if exit_code in _UNRUNNABLE_EXIT_CODES:
        return "environment"

    text = f"{stderr}\n{stdout}"
    if any(marker in text.lower() for marker in _UNRUNNABLE_MARKERS):
        return "environment"

    # A bare specifier is a dependency that was never installed (`react`); a
    # relative one is a file the worker was supposed to write (`./useFlashcards`),
    # which is its problem to fix.
    for spec in _MISSING_IMPORT.findall(text):
        if not spec.startswith((".", "/", "~")):
            return "environment"

    for spec in _MISSING_PY_MODULE.findall(text):
        top = spec.split(".")[0]
        if workspace_path and os.path.exists(os.path.join(workspace_path, top)):
            continue
        if workspace_path and os.path.exists(os.path.join(workspace_path, f"{top}.py")):
            continue
        return "environment"

    return "verification"


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
        stored = manifest_store.persist_task(
            manifest_path, task_id,
            stage="verified", verified_digest=digest, last_error=None
        )
        report.append(f"✅ `{task_id}`: {verification_cmd} passed.")
        return {
            "current_task": stored or current_task,
            "stage": "verified",
            "route": "audit",
            "test_run_passed": True,
            "test_stdout": stdout,
            "test_stderr": "",
            "tick_report": report,
            "error_message": ""
        }

    clean_err = sanitize_traceback(stderr) if stderr else stdout
    kind = classify_failure(exit_code, stdout, stderr, workspace_path)

    if kind == "environment":
        # The command could not run, so it says nothing about the code. Handing
        # this to the worker is what produced 25 rewrites of a correct
        # implementation: every attempt got the same dependency error back and
        # read it as its own bug. Halt and name the real problem instead — and
        # leave the implementation alone, because it is not what failed.
        message = (
            f"`{task_id}`: `{verification_cmd}` could not run in this worktree "
            f"(exit {exit_code}). This is an environment problem, not a coding one — "
            f"the command needs its dependencies installed before it can test anything. "
            f"Fix the command or the worktree setup and the task resumes.\n"
            f"```\n{clean_err[:500]}\n```"
        )
        stored = manifest_store.persist_task(
            manifest_path, task_id,
            status="halted",
            last_error={
                "stage": "verify", "kind": "environment",
                "message": f"exit {exit_code}: {clean_err[:500]}", "at": time.time()
            },
            lease_owner=None, lease_expires_at=None
        )
        report.append(f"⚠️ {message}")
        return {
            "current_task": stored or current_task,
            "test_run_passed": False,
            "test_stdout": stdout,
            "test_stderr": clean_err,
            "route": "done",
            "tick_report": report,
            "error_message": message
        }

    attempts = int((current_task.get("attempts") or {}).get("implement", 0))
    budget_left = attempts < MAX_IMPLEMENT_ATTEMPTS

    # A failing test is the one failure class that *should* spend an LLM attempt.
    stored = manifest_store.persist_task(
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
            # Carries the cleared digest and the real attempt count into the next
            # implement. Without it that node re-read the scheduler's opening
            # snapshot, saw zero attempts, and the pair never terminated.
            "current_task": stored or current_task,
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
        "current_task": stored or current_task,
        "test_run_passed": False,
        "test_stdout": stdout,
        "test_stderr": clean_err,
        "route": "done",
        "tick_report": report,
        "error_message": message
    }


async def _run(command: str, cwd: str):
    """Runs the verification command in the worktree, using the repo's own env."""
    return await run_in_worktree(command, cwd, timeout=VERIFY_TIMEOUT_SECONDS)
