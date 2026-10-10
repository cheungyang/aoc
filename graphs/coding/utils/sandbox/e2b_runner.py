"""The E2B backend: runs verification in a throwaway cloud sandbox.

The host never executes the code under test. Per run:

    create sandbox → fetch head_sha → [setup → purity check] → verify → kill

- **Source.** The sandbox downloads the exact pushed commit from GitHub. The host
  asks GitHub's API for the tarball and keeps the redirect instead of following
  it; that `codeload` URL carries a short-lived token scoped to one download,
  and it is the only credential that ever reaches the sandbox.
- **Exit codes are real.** Every step runs under `bash -o pipefail`, and this
  runner adds no pipes of its own: the Phase 0 spike watched `cmd | tail` report
  success for both a failing test run and an OOM-killed install. Output is
  truncated here, in Python, after the exit code is known.
- **Runner failures are not code failures.** A sandbox that will not start or a
  download that fails is reported as `infra_error`, which verify retries
  without charging the implement budget. A missing API key or SDK is a
  configuration problem and is reported as exit 126 ("could not run"), which
  halts instead of retrying something that cannot succeed.
- **Setup must not edit tracked files.** After `setup`, the sandbox's tree is
  compared against the commit; a setup that generated tracked files (a
  scaffold) fails with a message to move it to `scaffold_command`. Otherwise the
  result would depend on files that are not in the commit being verified.
- **The sandbox is always killed**, whatever happened. E2B also expires it on
  its own `timeout`, set just above the verify budget, as a backstop.
"""
import asyncio
import os
import shlex
import subprocess
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from graphs.coding.utils.sandbox.runner_types import RunResult, Step

API_KEY_ENV = "E2B_API_KEY"
API_KEY_FILE_ENV = "CODING_E2B_KEY_FILE"
TEMPLATE_ENV = "CODING_E2B_TEMPLATE"

SANDBOX_REPO_DIR = "/home/user/repo"
# E2B kills the sandbox itself this long after the verify budget runs out, in
# case this process dies before its own `kill`.
SANDBOX_GRACE_SECONDS = 120
# Enough for a test runner's summary and the failing tracebacks; what reaches
# a prompt is truncated again by `sanitize_traceback`.
OUTPUT_LIMIT = 20_000

_TIMEOUT_EXIT_CODE = 124
_CONFIG_EXIT_CODE = 126

SourceUrlFn = Callable[[str, str, str], Awaitable[str]]


class _ConfigError(Exception):
    """A problem no retry can fix: no API key, no SDK."""


def _tail(text: Optional[str]) -> str:
    text = text or ""
    return text if len(text) <= OUTPUT_LIMIT else "…(truncated)…\n" + text[-OUTPUT_LIMIT:]


def _wrap(command: str) -> str:
    """Runs `command` with pipefail, so a pipeline fails when any stage fails."""
    return f"bash -o pipefail -c {shlex.quote(command)}"


def read_api_key() -> str:
    """`E2B_API_KEY`, else the key file (`./e2b_api_key`, chmod 600, gitignored)."""
    key = (os.environ.get(API_KEY_ENV) or "").strip()
    if key:
        return key
    from graphs.coding.utils.repo import project_root

    path = os.environ.get(API_KEY_FILE_ENV) or os.path.join(project_root(), "e2b_api_key")
    try:
        with open(os.path.expanduser(path), "r", encoding="utf-8") as f:
            key = f.read().strip()
    except OSError:
        key = ""
    if not key:
        raise _ConfigError(
            f"No E2B API key: set ${API_KEY_ENV} or put the key in {path} (chmod 600)."
        )
    return key


def github_token(push_identity: Any = None) -> str:
    """A token for the tarball request: the machine user's, else the ambient `gh` login.

    Public repositories work without one; private ones need it.
    """
    token = getattr(push_identity, "token", "") or ""
    if token:
        return token
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(name):
            return os.environ[name]
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


async def tarball_url(repo: str, sha: str, token: str) -> str:
    """Asks GitHub for `repo@sha` as a tarball and returns the redirect target.

    Following the redirect would download the tarball here; the point is for
    the sandbox to download it, so only the short-lived codeload URL is kept.
    """
    import requests

    def _request() -> str:
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = requests.get(
            f"https://api.github.com/repos/{repo}/tarball/{sha}",
            headers=headers, allow_redirects=False, timeout=20,
        )
        location = resp.headers.get("Location")
        if resp.status_code not in (301, 302, 307) or not location:
            raise RuntimeError(f"GitHub returned {resp.status_code} for the {repo}@{sha[:7]} tarball")
        return location

    return await asyncio.to_thread(_request)


class E2BRunner:
    name = "e2b"
    # The sandbox starts empty, so the project's setup runs in it before the
    # tests. Locally the scheduler already ran setup in the worktree.
    runs_setup = True

    def __init__(
        self,
        *,
        template: Optional[str] = None,
        api_key: Optional[str] = None,
        sandbox_cls: Any = None,
        source_url: Optional[SourceUrlFn] = None,
    ):
        self._template = template or os.environ.get(TEMPLATE_ENV) or None
        self._api_key = api_key
        self._sandbox_cls = sandbox_cls
        self._source_url = source_url or tarball_url

    async def run(
        self,
        steps: List[Step],
        *,
        workspace_path: str,
        timeout: float,
        meta: Optional[Dict[str, Any]] = None,
    ) -> RunResult:
        """Runs `steps` in a fresh sandbox, stopping at the first that fails.

        `workspace_path` is ignored: the sandbox verifies the pushed commit,
        never this host's worktree. `meta` must carry `repo` and `head_sha`.
        """
        meta = meta or {}
        repo, sha = meta.get("repo") or "", meta.get("head_sha") or ""
        if not steps:
            return RunResult(0, "", "", backend=self.name)
        if not repo or not sha:
            return self._config(f"E2B verification needs the repo and the pushed commit (got repo={repo!r}, sha={sha!r}).")

        try:
            sandbox_cls, exit_exc, timeout_exc = self._sdk()
            api_key = self._api_key or read_api_key()
        except _ConfigError as e:
            return self._config(str(e))

        deadline = time.monotonic() + timeout
        try:
            url = await self._source_url(repo, sha, github_token(meta.get("push_identity")))
        except Exception as e:
            return self._infra(f"could not get a download URL for {repo}@{sha[:7]}: {e}")

        try:
            sbx = await sandbox_cls.create(
                template=self._template,
                timeout=int(timeout) + SANDBOX_GRACE_SECONDS,
                metadata={k: str(v) for k, v in (
                    ("task_id", meta.get("task_id")), ("head_sha", sha), ("repo", repo)
                ) if v},
                api_key=api_key,
            )
        except Exception as e:
            return self._infra(f"sandbox did not start: {e}")

        try:
            return await self._run_in(sbx, steps, url, deadline, timeout, exit_exc, timeout_exc)
        finally:
            try:
                await sbx.kill()
            except Exception as e:
                print(f"E2BRunner: could not kill sandbox (it expires on its own): {e}")

    async def _run_in(self, sbx, steps, url, deadline, timeout, exit_exc, timeout_exc) -> RunResult:
        async def execute(command: str, budget: float, cwd: str = SANDBOX_REPO_DIR, envs=None):
            try:
                r = await sbx.commands.run(_wrap(command), cwd=cwd, timeout=budget, envs=envs)
                return r.exit_code, r.stdout or "", r.stderr or ""
            except exit_exc as e:  # a non-zero exit is raised, not returned
                return e.exit_code, e.stdout or "", e.stderr or ""

        # 1. Fetch the commit. Two commands joined by `&&`, no pipe, so a failed
        #    download cannot be masked by a successful `tar`.
        fetch = (
            f"mkdir -p {SANDBOX_REPO_DIR} && "
            f'curl -fsSL --retry 2 -o /tmp/src.tgz "$SRC_URL" && '
            f"tar -xzf /tmp/src.tgz --strip-components=1 -C {SANDBOX_REPO_DIR} && "
            f"rm -f /tmp/src.tgz"
        )
        try:
            code, out, err = await execute(fetch, max(1.0, min(120.0, deadline - time.monotonic())),
                                           cwd="/", envs={"SRC_URL": url})
        except Exception as e:
            return self._infra(f"fetching the source failed: {e}")
        if code != 0:
            return self._infra(f"fetching the source failed (exit {code}): {_tail(err or out)}")

        # 2. Baseline for the setup purity check: the tarball has no `.git`.
        baseline = False
        if any(step.name == "setup" for step in steps):
            snapshot = (
                "git init -q && git add -A && "
                "git -c user.name=verify -c user.email=verify@localhost "
                "commit -q --no-verify --no-gpg-sign -m base"
            )
            try:
                code, _, _ = await execute(snapshot, max(1.0, min(60.0, deadline - time.monotonic())))
                baseline = code == 0
            except Exception:
                baseline = False

        # 3. The steps, against one shared deadline.
        stdout_parts: List[str] = []
        stderr = ""
        for step in steps:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return RunResult(
                    _TIMEOUT_EXIT_CODE, _tail("".join(stdout_parts)),
                    f"Timed out after {timeout:.0f}s before `{step.name}` could start.",
                    failed_step=step.name, backend=self.name,
                )
            try:
                code, out, err = await execute(step.command, remaining)
            except timeout_exc:
                return RunResult(
                    _TIMEOUT_EXIT_CODE, _tail("".join(stdout_parts)),
                    f"`{step.name}` timed out after {timeout:.0f}s total.",
                    failed_step=step.name, backend=self.name,
                )
            except Exception as e:
                return self._infra(f"the sandbox failed during `{step.name}`: {e}")

            stdout_parts.append(out)
            stderr = err
            if code != 0:
                return RunResult(code, _tail("".join(stdout_parts)), _tail(err),
                                 failed_step=step.name, backend=self.name)

            if step.name == "setup" and baseline:
                dirty = await self._tracked_changes(execute, deadline)
                if dirty:
                    return RunResult(
                        1, _tail("".join(stdout_parts)),
                        "`setup_command` changed tracked files, so the tests would not be testing "
                        "the pushed commit. Move whatever generates them to `scaffold_command` "
                        "(run once, committed) and keep setup to ignored paths such as "
                        f"`node_modules/` or `.venv/`:\n{dirty}",
                        failed_step="setup", backend=self.name,
                    )

        return RunResult(0, _tail("".join(stdout_parts)), _tail(stderr), backend=self.name)

    async def _tracked_changes(self, execute, deadline) -> str:
        """`git status --porcelain` after setup; "" when clean or when it cannot tell."""
        try:
            code, out, _ = await execute(
                "git status --porcelain --untracked-files=all",
                max(1.0, min(60.0, deadline - time.monotonic())),
            )
        except Exception:
            return ""
        if code != 0:
            return ""
        lines = [line for line in out.splitlines() if line.strip()]
        shown = "\n".join(lines[:20]) + (f"\n… and {len(lines) - 20} more" if len(lines) > 20 else "")
        return shown if lines else ""

    def _sdk(self):
        if self._sandbox_cls is not None:
            from graphs.coding.utils.sandbox import e2b_runner as _self
            return self._sandbox_cls, _self._exit_exception(), _self._timeout_exception()
        try:
            from e2b import AsyncSandbox, CommandExitException, TimeoutException
        except ImportError as e:
            raise _ConfigError(f"The `e2b` package is not installed on this host ({e}).")
        return AsyncSandbox, CommandExitException, TimeoutException

    def _infra(self, message: str) -> RunResult:
        return RunResult(1, "", message, infra_error=message, backend=self.name)

    def _config(self, message: str) -> RunResult:
        return RunResult(_CONFIG_EXIT_CODE, "", f"E2B backend misconfigured: {message}",
                         backend=self.name)


def _exit_exception():
    try:
        from e2b import CommandExitException
        return CommandExitException
    except ImportError:
        return _NeverRaised


def _timeout_exception():
    try:
        from e2b import TimeoutException
        return TimeoutException
    except ImportError:
        return _NeverRaised


class _NeverRaised(Exception):
    """Stands in for an SDK exception type when the SDK is absent (tests)."""
