import os
import asyncio
from typing import Dict, Any
from graphs.coding.schemas import CodingState

async def tester_node(state: CodingState) -> Dict[str, Any]:
    """
    Deterministic Tester Node:
    Executes the verification command directly in the isolated worktree via subprocess.
    LLM self-evaluations are NEVER trusted for code execution correctness.
    """
    workspace_path = state.get("workspace_path") or os.getcwd()
    current_task = state.get("current_task") or {}
    verification_cmd = current_task.get("verification_command", "").strip()
    current_attempt = state.get("attempt_count", 0)

    if not verification_cmd:
        err = f"Missing verification_command for task {current_task.get('task_id', 'unknown')}. Deterministic tester requires an explicit CLI test command."
        return {
            "test_run_passed": False,
            "test_stdout": "",
            "test_stderr": err,
            "attempt_count": current_attempt + 1,
            "error_message": err
        }

    # Ensure workspace path exists
    if not os.path.exists(workspace_path):
        err = f"Workspace path does not exist: {workspace_path}"
        return {
            "test_run_passed": False,
            "test_stdout": "",
            "test_stderr": err,
            "attempt_count": current_attempt + 1,
            "error_message": err
        }

    # Execute verification command with 120-second timeout
    env = os.environ.copy()
    
    try:
        proc = await asyncio.create_subprocess_shell(
            verification_cmd,
            cwd=workspace_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=120.0)
        exit_code = proc.returncode or 0
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        exit_code = 124
        stdout = ""
        stderr = f"Test execution timed out after 120 seconds: {verification_cmd}"
    except Exception as e:
        exit_code = 1
        stdout = ""
        stderr = f"Test process execution failed: {e}"

    passed = (exit_code == 0)
    new_attempt = current_attempt if passed else current_attempt + 1

    return {
        "test_run_passed": passed,
        "test_stdout": stdout,
        "test_stderr": stderr if not passed else "",
        "attempt_count": new_attempt
    }
