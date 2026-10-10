"""What a verification runner is, independent of where it runs.

The verify node used to call `run_in_worktree` directly, which hard-wired
"tests run on this machine". Pulling the contract out lets the same node drive a
local subprocess today and a remote sandbox later without its routing — or
`classify_failure`, which reads exit codes and output — knowing the difference.

Two outcomes are kept apart on purpose:

- the command ran and reported something (`exit_code`, `stdout`, `stderr`) —
  that says something about the code;
- the runner itself could not do its job (`infra_error`) — the sandbox did not
  start, the network dropped. That says nothing about the code, and must never
  be charged to the implement budget.
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable


@dataclass(frozen=True)
class Step:
    """One command in a verification run. `name` is what a failure is attributed to."""
    name: str
    command: str


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    # Set only when the runner failed, not the command. When set, the exit code
    # and output are meaningless and must not be classified.
    infra_error: Optional[str] = None
    # Name of the step that failed (e.g. "setup", "verify"); None on success.
    failed_step: Optional[str] = None
    backend: str = ""

    @property
    def passed(self) -> bool:
        return self.infra_error is None and self.exit_code == 0

    def as_tuple(self) -> Tuple[int, str, str]:
        """The `(exit_code, stdout, stderr)` shape `run_in_worktree` returns."""
        return self.exit_code, self.stdout, self.stderr


@runtime_checkable
class VerifyRunner(Protocol):
    """Runs verification steps against a task's code and reports the outcome.

    Implementations must never raise for an ordinary failure: a command that
    exits non-zero is a `RunResult`, and a runner that cannot do its job sets
    `infra_error`. A traceback here would abort the tick instead of halting
    one task.
    """

    name: str

    async def run(
        self,
        steps: List[Step],
        *,
        workspace_path: str,
        timeout: float,
        meta: Optional[Dict[str, Any]] = None,
    ) -> RunResult:
        ...
