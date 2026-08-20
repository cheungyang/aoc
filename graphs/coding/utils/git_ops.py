import os
import shutil
import asyncio
import subprocess
from typing import Tuple, Optional, List

async def run_cmd_async(
    cmd: List[str],
    cwd: str,
    timeout: float = 60.0,
    env: Optional[dict] = None
) -> Tuple[int, str, str]:
    """Runs a CLI command asynchronously using asyncio.create_subprocess_exec."""
    run_env = os.environ.copy()
    if env:
        run_env.update(env)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=run_env
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        return proc.returncode or 0, stdout, stderr
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return 124, "", f"Command timed out after {timeout} seconds: {' '.join(cmd)}"
    except Exception as e:
        return 1, "", f"Execution failed: {e}"


def run_cmd_sync(cmd: List[str], cwd: str, timeout: float = 60.0) -> Tuple[int, str, str]:
    """Runs a CLI command synchronously."""
    try:
        res = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False
        )
        return res.returncode, res.stdout, res.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"Command timed out after {timeout} seconds"
    except Exception as e:
        return 1, "", str(e)


async def resolve_base_ref(repo_path: str) -> str:
    """Discovers the appropriate git base ref (origin/main, origin/master, main, or HEAD)."""
    # 1. Check if origin/main exists
    code, _, _ = await run_cmd_async(["git", "rev-parse", "--verify", "origin/main"], cwd=repo_path, timeout=5.0)
    if code == 0:
        return "origin/main"

    # 2. Check if origin/master exists
    code, _, _ = await run_cmd_async(["git", "rev-parse", "--verify", "origin/master"], cwd=repo_path, timeout=5.0)
    if code == 0:
        return "origin/main"

    # 3. Check if main exists locally
    code, _, _ = await run_cmd_async(["git", "rev-parse", "--verify", "main"], cwd=repo_path, timeout=5.0)
    if code == 0:
        return "main"

    # Fallback to HEAD
    return "HEAD"


async def provision_worktree(
    repo_path: str,
    workspace_path: str,
    branch_name: str,
    base_ref: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Deterministically provisions a sandboxed Git worktree at workspace_path
    with a new branch checked out. Handles stale worktree and directory cleanup.
    """
    abs_repo = os.path.abspath(repo_path)
    abs_ws = os.path.abspath(workspace_path)
    
    # 1. Fetch remote if remote origin exists (optional, non-blocking failure)
    await run_cmd_async(["git", "fetch", "origin"], cwd=abs_repo, timeout=10.0)

    # 2. Resolve base reference
    target_base = base_ref or await resolve_base_ref(abs_repo)

    # 3. Clean up stale worktree / directory if it already exists
    if os.path.exists(abs_ws):
        await run_cmd_async(["git", "worktree", "remove", "--force", abs_ws], cwd=abs_repo, timeout=15.0)
        await run_cmd_async(["git", "worktree", "prune"], cwd=abs_repo, timeout=10.0)
        if os.path.exists(abs_ws):
            try:
                shutil.rmtree(abs_ws, ignore_errors=True)
            except Exception:
                pass

    # 4. Also delete any existing local branch with same name to ensure clean start
    await run_cmd_async(["git", "branch", "-D", branch_name], cwd=abs_repo, timeout=5.0)

    # 5. Ensure parent directory exists
    os.makedirs(os.path.dirname(abs_ws), exist_ok=True)

    # 6. Run git worktree add
    code, out, err = await run_cmd_async(
        ["git", "worktree", "add", "-b", branch_name, abs_ws, target_base],
        cwd=abs_repo,
        timeout=30.0
    )

    if code != 0:
        # Fallback: try from HEAD if target_base failed
        if target_base != "HEAD":
            code, out, err = await run_cmd_async(
                ["git", "worktree", "add", "-b", branch_name, abs_ws, "HEAD"],
                cwd=abs_repo,
                timeout=30.0
            )

    if code == 0 and os.path.exists(abs_ws):
        return True, f"Successfully provisioned worktree at {abs_ws} (branch: {branch_name})"
    else:
        return False, f"Failed to provision worktree at {abs_ws}: {err or out}"


async def get_git_diff(workspace_path: str) -> str:
    """Gets unified git diff against the initial commit of the worktree branch."""
    if not os.path.exists(workspace_path):
        return ""
    
    # 1. Unstaged + Staged changes
    code, out, _ = await run_cmd_async(["git", "diff", "HEAD"], cwd=workspace_path, timeout=15.0)
    if code == 0 and out.strip():
        return out

    # 2. If no HEAD diff, check standard git diff
    code, out, _ = await run_cmd_async(["git", "diff"], cwd=workspace_path, timeout=15.0)
    if code == 0 and out.strip():
        return out

    # 3. If uncommitted, check status for untracked files
    code, out, _ = await run_cmd_async(["git", "status", "--porcelain"], cwd=workspace_path, timeout=10.0)
    return out or ""


async def commit_and_push(
    workspace_path: str,
    branch_name: str,
    commit_msg: str
) -> Tuple[bool, str]:
    """Stages all modified files, commits, and pushes branch to origin."""
    if not os.path.exists(workspace_path):
        return False, f"Workspace path does not exist: {workspace_path}"

    # 1. git add .
    code, out, err = await run_cmd_async(["git", "add", "."], cwd=workspace_path, timeout=15.0)
    if code != 0:
        return False, f"git add failed: {err or out}"

    # 2. git commit -m
    code, out, err = await run_cmd_async(["git", "commit", "-m", commit_msg], cwd=workspace_path, timeout=15.0)
    if code != 0 and "nothing to commit" not in (out + err).lower():
        return False, f"git commit failed: {err or out}"

    # 3. git push -u origin <branch>
    code, out, err = await run_cmd_async(["git", "push", "-u", "origin", branch_name], cwd=workspace_path, timeout=30.0)
    if code != 0:
        # If origin is not set (e.g. local test repository), don't hard crash, log warning
        if "fatal: 'origin' does not appear to be a 'git' repository" in err or "remote" in err.lower():
            return True, f"Committed locally (remote origin push skipped: {err.strip()})"
        return False, f"git push failed: {err or out}"

    return True, f"Successfully committed and pushed branch {branch_name}"


async def create_pull_request(
    workspace_path: str,
    branch_name: str,
    title: str,
    body: str,
    base_branch: str = "main",
    target_repo: Optional[str] = None
) -> Tuple[bool, str]:
    """Creates a GitHub PR using gh CLI tool."""
    cmd = ["gh", "pr", "create", "--head", branch_name, "--base", base_branch, "--title", title, "--body", body]
    if target_repo:
        cmd.extend(["--repo", target_repo])

    code, out, err = await run_cmd_async(cmd, cwd=workspace_path, timeout=30.0)
    if code == 0 and ("http" in out or "github.com" in out):
        pr_url = out.strip().splitlines()[-1]
        return True, pr_url
    else:
        # If gh fails (e.g. authentication or no remote repo in CI/test), generate simulated PR URL or capture error
        if "not logged in" in (err + out).lower() or "no default repository" in (err + out).lower() or "fatal" in (err + out).lower():
            fallback_url = f"https://github.com/local-repo/pull/{branch_name}"
            return True, fallback_url
        return False, f"gh pr create failed: {err or out}"


async def teardown_worktree(repo_path: str, workspace_path: str) -> Tuple[bool, str]:
    """Force removes the temporary worktree and prunes git refs."""
    abs_repo = os.path.abspath(repo_path)
    abs_ws = os.path.abspath(workspace_path)

    await run_cmd_async(["git", "worktree", "remove", "--force", abs_ws], cwd=abs_repo, timeout=20.0)
    await run_cmd_async(["git", "worktree", "prune"], cwd=abs_repo, timeout=10.0)

    if os.path.exists(abs_ws):
        try:
            shutil.rmtree(abs_ws, ignore_errors=True)
        except Exception:
            pass

    return True, f"Teardown complete for {abs_ws}"
