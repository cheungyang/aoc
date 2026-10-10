"""The local backend: runs verification steps as subprocesses on this machine.

This is exactly what verify did before runners existed, kept as the default and
as the escape hatch when a remote backend is unavailable. It deliberately goes
through `run_in_worktree`, so setup and verify still share one definition of
exit code, output and timeout.
"""
import time
from typing import Any, Dict, List, Optional

from graphs.coding.utils.sandbox.runner_types import RunResult, Step
from graphs.coding.utils.shell import run_in_worktree

# `run_in_worktree` reports a timeout as 124, like coreutils `timeout`.
_TIMEOUT_EXIT_CODE = 124


class LocalRunner:
    name = "local"

    async def run(
        self,
        steps: List[Step],
        *,
        workspace_path: str,
        timeout: float,
        meta: Optional[Dict[str, Any]] = None,
    ) -> RunResult:
        """Runs `steps` in order, stopping at the first that fails.

        `timeout` is the budget for the whole run, not per step: a slow setup
        leaves less time for the tests rather than doubling the wall clock.
        Output is accumulated across steps so a failure in a later step still
        shows what the earlier ones printed.
        """
        if not steps:
            return RunResult(0, "", "", backend=self.name)

        deadline = time.monotonic() + timeout
        stdout_parts: List[str] = []
        stderr = ""
        for step in steps:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return RunResult(
                    _TIMEOUT_EXIT_CODE, "".join(stdout_parts),
                    f"Timed out after {timeout:.0f}s before `{step.name}` could start.",
                    failed_step=step.name, backend=self.name,
                )
            code, out, err = await run_in_worktree(step.command, workspace_path, timeout=remaining)
            stdout_parts.append(out)
            stderr = err
            if code != 0:
                return RunResult(code, "".join(stdout_parts), err,
                                 failed_step=step.name, backend=self.name)
        return RunResult(0, "".join(stdout_parts), stderr, backend=self.name)
