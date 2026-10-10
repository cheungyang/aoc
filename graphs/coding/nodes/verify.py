"""verify — runs the task's verification command in the worktree.

The only blocking quality gate (§7). Two things it deliberately does not do:

- it does not require a PR to exist. Gating local tests behind a network
  operation is what turned a GitHub hiccup into "tests failed" (A3);
- it does not re-run for a commit it has already tested. It tests the pushed
  `head_sha` and records it as `verified_sha`, so a resumed tick — on this host
  or another — goes straight to publish.

It also does not hand every red run back to the worker. A command that fails
because the toolchain is not installed fails identically no matter what the
code says, so retrying it spends the whole implement budget rewriting code that
was never wrong. The same goes for the runner itself: a sandbox that would not
start (`infra`) is retried on a later tick without touching the code.

Where the command runs is the backend's business (`environment.backend` in the
manifest, else `$CODING_VERIFY_BACKEND`). A remote backend starts from nothing,
so it also runs the project's `setup_command` before the tests.
"""
import os
import re
import time
from typing import Any, Dict, Optional

from graphs.coding.schemas import CodingState
from graphs.coding.utils import manifest as manifest_store
from graphs.coding.utils.dag import resolve_manifest_path
from graphs.coding.utils.repo import get_push_identity, get_repo_descriptor
from graphs.coding.utils.sandbox import RunResult, Step, UnknownBackendError, get_runner
from graphs.coding.utils.token_opt import sanitize_traceback

# Ten minutes. The old 120s assumed the command was only a test run, but a
# verification command now routinely installs its own dependencies first, and a
# cold `npm install` alone can outlast two minutes. A timeout is charged to the
# implement budget as though the code were at fault, so a budget too short does
# not just slow the pipeline down — it halts correct work.
VERIFY_TIMEOUT_SECONDS = 600.0
MAX_IMPLEMENT_ATTEMPTS = 3
# Runner failures (sandbox did not start, source download failed) before a
# human is asked to look. They say nothing about the code.
MAX_INFRA_ATTEMPTS = 3

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

    head_sha = current_task.get("head_sha") or ""

    # Guard: this exact commit already passed.
    if (
        manifest_store.stage_at_or_past(current_task, "verified")
        and head_sha
        and current_task.get("verified_sha") == head_sha
    ):
        report.append(f"⏩ `{task_id}` already verified at `{head_sha[:7]}`; skipping tests.")
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

    if not head_sha:
        # Only pushed code is verified: the commit is what the sandbox fetches
        # and what the result is recorded against. Send it back through push.
        message = f"`{task_id}`: no pushed commit to verify."
        manifest_store.yield_task(
            manifest_path, task_id,
            stage="implemented",
            last_error={"stage": "verify", "kind": "git", "message": message, "at": time.time()}
        )
        report.append(f"⚠️ {message} Pushing again on the next tick.")
        return {"route": "done", "test_run_passed": False, "tick_report": report, "error_message": message}

    if not os.path.exists(workspace_path):
        # The code is safe on the remote branch, so keep the stage: the next
        # tick re-provisions the worktree from `origin/<branch>` and verifies.
        message = f"`{task_id}`: workspace {workspace_path} does not exist."
        manifest_store.yield_task(
            manifest_path, task_id,
            last_error={"stage": "verify", "kind": "git", "message": message, "at": time.time()}
        )
        report.append(f"⚠️ {message} Re-provisioning on the next tick.")
        return {"route": "done", "test_run_passed": False, "tick_report": report, "error_message": message}

    environment, setup_command = _execution_config(manifest_path, current_task)
    result = await _run(
        verification_cmd, workspace_path,
        meta={
            "task_id": task_id,
            "head_sha": head_sha,
            "repo": get_repo_descriptor(state).get("slug") or "",
            "push_identity": get_push_identity(state),
        },
        setup_command=setup_command,
        environment=environment,
    )
    exit_code, stdout, stderr = result.as_tuple()
    passed = exit_code == 0

    if result.infra_error:
        # The runner failed, not the code: keep the stage and the implement
        # budget, and try again on a later tick.
        count = manifest_store.bump_attempt(manifest_path, task_id, "infra")
        backend = result.backend or "verify"
        last_error = {"stage": "verify", "kind": "infra",
                      "message": result.infra_error[:500], "at": time.time()}
        if count < MAX_INFRA_ATTEMPTS:
            stored = manifest_store.yield_task(
                manifest_path, task_id, stage="pushed", last_error=last_error
            )
            report.append(
                f"🌩️ `{task_id}`: the {backend} runner failed ({result.infra_error[:200]}); "
                f"retrying next tick ({count}/{MAX_INFRA_ATTEMPTS})."
            )
            message = ""
        else:
            stored = manifest_store.persist_task(
                manifest_path, task_id, status="halted", last_error=last_error,
                lease_owner=None, lease_expires_at=None
            )
            message = (
                f"`{task_id}`: the {backend} runner failed {count} times "
                f"({result.infra_error[:300]}). Halted; the code was not tested."
            )
            report.append(f"⚠️ {message}")
        return {"current_task": stored or current_task, "route": "done", "test_run_passed": False,
                "test_stdout": stdout, "test_stderr": stderr, "tick_report": report,
                "error_message": message}

    if passed:
        # The infra budget counts runner failures since the last run that got
        # through, so old blips do not add up to a halt weeks later.
        attempts = dict(current_task.get("attempts") or {})
        reset = {"attempts": {k: v for k, v in attempts.items() if k != "infra"}} if "infra" in attempts else {}
        stored = manifest_store.persist_task(
            manifest_path, task_id,
            stage="verified", verified_sha=head_sha, last_error=None, **reset
        )
        report.append(f"✅ `{task_id}`: {verification_cmd} passed at `{head_sha[:7]}`.")
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
    setup_failed = result.failed_step == "setup"
    kind = "environment" if setup_failed else classify_failure(exit_code, stdout, stderr, workspace_path)

    if kind == "environment":
        # The command could not run, so it says nothing about the code. Handing
        # this to the worker is what produced 25 rewrites of a correct
        # implementation: every attempt got the same dependency error back and
        # read it as its own bug. Halt and name the real problem instead — and
        # leave the implementation alone, because it is not what failed.
        if setup_failed:
            message = (
                f"`{task_id}`: `setup_command` failed in the {result.backend or 'verify'} "
                f"sandbox (exit {exit_code}), so the tests never ran. This is an environment "
                f"problem, not a coding one. Fix the project's setup and the task resumes.\n"
                f"```\n{clean_err[:500]}\n```"
            )
        else:
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


def _execution_config(manifest_path: str, task: Dict[str, Any]):
    """(`environment` block, `setup_command`) for this task, task-level first."""
    try:
        manifest = manifest_store.load_manifest(manifest_path) if manifest_path else {}
    except ValueError:
        manifest = {}
    environment = task.get("environment") or manifest.get("environment") or None
    setup_command = task.get("setup_command") or manifest.get("setup_command") or None
    return environment, setup_command


async def _run(
    command: str,
    cwd: str,
    meta: Optional[Dict[str, Any]] = None,
    setup_command: Optional[str] = None,
    environment: Optional[Dict[str, Any]] = None,
) -> RunResult:
    """Runs the verification command through the configured backend.

    A backend that starts from nothing (`runs_setup`) gets the project's
    `setup_command` as its first step; locally the scheduler already ran it in
    the worktree. A misconfigured backend is reported as exit 126 ("could not
    run"), which `classify_failure` treats as an environment problem: the task
    halts with the configuration message instead of spending an implement attempt.
    """
    try:
        runner = get_runner(environment)
    except UnknownBackendError as e:
        return RunResult(126, "", f"Verify backend misconfigured: {e}")
    steps = [Step("verify", command)]
    if setup_command and getattr(runner, "runs_setup", False) is True:
        steps.insert(0, Step("setup", str(setup_command)))
    return await runner.run(
        steps, workspace_path=cwd, timeout=VERIFY_TIMEOUT_SECONDS, meta=meta or {}
    )
