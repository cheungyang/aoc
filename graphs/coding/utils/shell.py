"""Running a shell command inside a worktree.

One implementation, because `setup` and `verify` have to agree on what a
command's exit code, output and timeout mean. Two copies would drift, and the
difference between "the command failed" and "the command could not run" is the
distinction the whole retry budget rests on.
"""
import asyncio
import os
from typing import Tuple

# Ten minutes. Long enough for a cold dependency install, which is the slowest
# thing either caller legitimately does.
DEFAULT_TIMEOUT_SECONDS = 600.0


async def run_in_worktree(
    command: str,
    cwd: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> Tuple[int, str, str]:
    """Runs `command` in `cwd` with the repo's own environment.

    Returns `(exit_code, stdout, stderr)`. Never raises: a command that could
    not start is reported as a failure with the reason in stderr, because a
    traceback here would abort the tick instead of halting one task.
    """
    proc = None
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy()
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        return (
            proc.returncode or 0,
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace")
        )
    except asyncio.TimeoutError:
        try:
            if proc is not None:
                proc.kill()
                await proc.wait()
        except Exception:
            pass
        return 124, "", f"Timed out after {timeout:.0f}s: {command}"
    except Exception as e:
        return 1, "", f"Could not run: {e}"
