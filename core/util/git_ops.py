"""Git operations utility module providing asynchronous and synchronous Git and GitHub operations."""
import os
import shutil
import asyncio
import subprocess
from typing import Tuple, Optional, List, Dict, Any

from core.util.push_identity import (
    PushIdentity,
    git_commit_args,
    git_credential_args,
    subprocess_env,
)

# Several operations used to report success when the remote was unreachable or `gh` was
# unauthenticated: a push that never left the machine, a fabricated PR URL, a "simulated"
# merge and a "simulated" comment. Downstream nodes trusted those results and carried on,
# so a run could report a merged PR that did not exist. Honest failure is now the default;
# the old behaviour is opt-in for offline experiments only.
_SIMULATION_ENV_VAR = "ALLOW_SIMULATED_GIT"


def simulated_git_allowed() -> bool:
    """True only when ALLOW_SIMULATED_GIT is explicitly enabled."""
    return os.environ.get(_SIMULATION_ENV_VAR, "").strip().lower() in ("1", "true", "yes", "on")


def _with_identity_env(
    identity: Optional[PushIdentity],
    env: Optional[dict] = None
) -> Optional[dict]:
    """Merges the machine user's token into one subprocess's environment.

    The token is scoped to the subprocess rather than exported globally, so the
    human's own `gh auth login` session is never overridden.
    """
    overrides = subprocess_env(identity)
    if not overrides:
        return env
    merged = dict(env or {})
    merged.update(overrides)
    return merged



async def run_cmd_async(
    cmd: List[str],
    cwd: str,
    timeout: float = 60.0,
    env: Optional[dict] = None
) -> Tuple[int, str, str]:
    """Runs a CLI command asynchronously using asyncio.create_subprocess_exec."""
    run_env = os.environ.copy()
    run_env["GIT_TERMINAL_PROMPT"] = "0"
    run_env["GIT_ASKPASS"] = ""
    run_env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10")
    if env:
        run_env.update(env)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
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


def run_cmd_sync(cmd: List[str], cwd: str, timeout: float = 60.0, env: Optional[dict] = None) -> Tuple[int, str, str]:
    """Runs a CLI command synchronously."""
    run_env = os.environ.copy()
    run_env["GIT_TERMINAL_PROMPT"] = "0"
    run_env["GIT_ASKPASS"] = ""
    run_env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10")
    if env:
        run_env.update(env)
    try:
        res = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
            env=run_env
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
        return "origin/master"

    # 3. Check if main exists locally
    code, _, _ = await run_cmd_async(["git", "rev-parse", "--verify", "main"], cwd=repo_path, timeout=5.0)
    if code == 0:
        return "main"

    # Fallback to HEAD
    return "HEAD"


_repo_locks: Dict[str, asyncio.Lock] = {}


def repo_lock(repo_path: str) -> asyncio.Lock:
    """One lock per repository, for the operations that contend on `.git/index.lock`.

    `git worktree add/remove/prune` and `git fetch` all take that lock, so two
    concurrent ticks against the same repo fail with a lock error rather than
    queueing. Concurrency is 1 today, but the lease model is built for N and
    this is the piece that makes N safe.
    """
    key = os.path.realpath(repo_path)
    lock = _repo_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _repo_locks[key] = lock
    return lock


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

    async with repo_lock(abs_repo):
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

        # 6. Run git worktree add.
        #    No fallback to HEAD on failure: branching from whatever happens to be
        #    checked out silently builds the work on the wrong commit (F3). A
        #    missing base ref is a real problem and is reported as one.
        code, out, err = await run_cmd_async(
            ["git", "worktree", "add", "-b", branch_name, abs_ws, target_base],
            cwd=abs_repo,
            timeout=30.0
        )

    if code == 0 and os.path.exists(abs_ws):
        return True, f"Successfully provisioned worktree at {abs_ws} (branch: {branch_name})"
    return False, (
        f"Failed to provision worktree at {abs_ws} from base ref `{target_base}`: {err or out}"
    )



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
    commit_msg: str,
    author: Optional[str] = "Graph Worker <worker@egm.internal>",
    push_identity: Optional[PushIdentity] = None
) -> Tuple[bool, str]:
    """Stages all modified files, commits with author attribution, and pushes branch to origin.

    When `push_identity` is supplied the commit is attributed to that machine user
    and the push authenticates with its token, so the PR is authored by the bot and
    the human reviewer can use GitHub's native Approve.
    """
    if not os.path.exists(workspace_path):
        return False, f"Workspace path does not exist: {workspace_path}"

    # Setup git environment variables for committer/author identity and non-interactive SSH
    commit_env = {
        "GIT_AUTHOR_NAME": "Graph Worker",
        "GIT_AUTHOR_EMAIL": "worker@egm.internal",
        "GIT_COMMITTER_NAME": "Graph Worker",
        "GIT_COMMITTER_EMAIL": "worker@egm.internal",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "",
        "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10",
    }
    # The machine user owns the history it creates, so it outranks the caller's default.
    effective_author = push_identity.author if push_identity else author
    if effective_author:
        import re
        m = re.match(r"^(.*?)\s*<([^>]+)>$", effective_author.strip())
        if m:
            name, email = m.group(1).strip(), m.group(2).strip()
            if name:
                commit_env["GIT_AUTHOR_NAME"] = name
                commit_env["GIT_COMMITTER_NAME"] = name
            if email:
                commit_env["GIT_AUTHOR_EMAIL"] = email
                commit_env["GIT_COMMITTER_EMAIL"] = email

    commit_env = _with_identity_env(push_identity, commit_env)
    identity_cfg = git_commit_args(push_identity)
    credential_cfg = git_credential_args(push_identity)

    # 1. git add .
    code, out, err = await run_cmd_async(["git", "add", "."], cwd=workspace_path, timeout=15.0, env=commit_env)
    if code != 0:
        return False, f"git add failed: {err or out}"

    # 2. git commit -m with --no-verify and --no-gpg-sign
    commit_cmd = ["git"] + identity_cfg + ["commit", "--no-verify", "--no-gpg-sign", "-m", commit_msg]
    if effective_author:
        commit_cmd.append(f"--author={effective_author}")

    code, out, err = await run_cmd_async(commit_cmd, cwd=workspace_path, timeout=15.0, env=commit_env)
    if code != 0 and "nothing to commit" not in (out + err).lower():
        # Fallback: retry commit using GIT_AUTHOR_* / GIT_COMMITTER_* environment variables
        retry_cmd = ["git"] + identity_cfg + ["commit", "--no-verify", "--no-gpg-sign", "-m", commit_msg]
        code2, out2, err2 = await run_cmd_async(retry_cmd, cwd=workspace_path, timeout=15.0, env=commit_env)
        if code2 == 0 or "nothing to commit" in (out2 + err2).lower():
            code, out, err = code2, out2, err2
        else:
            return False, f"git commit failed: {err or out}"

    # 3. git push -u origin <branch>
    push_cmd = ["git"] + credential_cfg + ["push", "-u", "origin", branch_name]
    code, out, err = await run_cmd_async(push_cmd, cwd=workspace_path, timeout=30.0, env=commit_env)
    if code != 0:
        detail = (err.strip() or out.strip())
        if simulated_git_allowed():
            return True, f"Committed locally (remote origin push skipped: {detail})"
        # The commit is safe on the local branch; the caller must not treat this as published.
        return False, (
            f"git push failed for branch {branch_name}: {detail}. "
            f"The commit is preserved locally at {workspace_path}."
        )

    return True, f"Successfully committed and pushed branch {branch_name}"


async def discover_target_repo(
    workspace_path: str,
    project_path: Optional[str] = None
) -> Optional[str]:
    """
    Discovers the GitHub target repo (owner/repo) from git remote origin in workspace_path or project_path.
    """
    for check_dir in [workspace_path, project_path]:
        if not check_dir or not os.path.exists(check_dir):
            continue
        code, out, _ = await run_cmd_async(["git", "remote", "get-url", "origin"], cwd=check_dir, timeout=5.0)
        if code != 0 or not out.strip():
            code, out, _ = await run_cmd_async(["git", "config", "--get", "remote.origin.url"], cwd=check_dir, timeout=5.0)
        if code == 0 and out.strip():
            raw_url = out.strip()
            import re
            m = re.search(r'github\.com[:/]([a-zA-Z0-9_\-\.]+/[a-zA-Z0-9_\-]+?)(?:\.git)?$', raw_url)
            if m:
                return m.group(1)
    return None


async def create_pull_request(
    workspace_path: str,
    branch_name: str,
    title: str,
    body: str,
    base_branch: str = "main",
    target_repo: Optional[str] = None,
    push_identity: Optional[PushIdentity] = None
) -> Tuple[bool, str, Optional[int]]:
    """Creates a GitHub PR using gh CLI tool and returns (success, pr_url, pr_number)."""
    resolved_repo = target_repo or await discover_target_repo(workspace_path)
    cmd = ["gh", "pr", "create", "--head", branch_name, "--base", base_branch, "--title", title, "--body", body]
    if resolved_repo:
        cmd.extend(["--repo", resolved_repo])

    code, out, err = await run_cmd_async(
        cmd, cwd=workspace_path, timeout=30.0, env=_with_identity_env(push_identity)
    )
    combined = out + " " + err
    import re

    # 1. Successful PR creation or URL in output
    urls = re.findall(r'https?://github\.com/[^\s/]+/[^\s/]+/pull/\d+', combined)
    if urls:
        pr_url = urls[-1]
        m = re.search(r'/pull/(\d+)', pr_url)
        pr_number = int(m.group(1)) if m else None
        return True, pr_url, pr_number

    if code == 0 and ("http" in out or "github.com" in out):
        pr_url = out.strip().splitlines()[-1].strip()
        m = re.search(r'/pull/(\d+)', pr_url)
        pr_number = int(m.group(1)) if m else None
        return True, pr_url, pr_number

    # 2. Check if a PR already exists for the branch (idempotent retry)
    if "already exists" in combined.lower():
        m = re.search(r'https?://github\.com/[^\s/]+/[^\s/]+/pull/(\d+)', combined)
        if m:
            pr_url = m.group(0)
            pr_number = int(m.group(1))
            return True, pr_url, pr_number

    # 3. Offline / unauthenticated environments: only ever fabricate a URL on explicit opt-in.
    if "not logged in" in combined.lower() or "no default repository" in combined.lower() or "fatal" in combined.lower():
        if simulated_git_allowed():
            repo_slug = resolved_repo or "local-repo"
            return True, f"https://github.com/{repo_slug}/pull/{branch_name}", None
        return False, f"gh pr create failed: {(err or out).strip()}", None

    return False, f"gh pr create failed: {err or out}", None


async def get_pull_request_status(
    workspace_path: str,
    pr_number_or_url: str,
    target_repo: Optional[str] = None,
    push_identity: Optional[PushIdentity] = None
) -> Dict[str, Any]:
    """Queries GitHub PR state, review decision, labels, reviews and comments via gh CLI."""
    import json
    cmd = [
        "gh", "pr", "view", str(pr_number_or_url),
        "--json", "state,reviewDecision,comments,url,number,mergeCommit,labels,latestReviews"
    ]
    resolved_repo = target_repo or await discover_target_repo(workspace_path)
    if resolved_repo and not str(pr_number_or_url).startswith("http"):
        cmd.extend(["--repo", resolved_repo])

    code, out, err = await run_cmd_async(
        cmd, cwd=workspace_path, timeout=15.0, env=_with_identity_env(push_identity)
    )
    if code == 0:
        try:
            return json.loads(out)
        except Exception:
            pass

    return {
        "state": "OPEN",
        "reviewDecision": "",
        "comments": [],
        "url": str(pr_number_or_url),
        "number": None
    }


_REVIEW_THREADS_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) {
        nodes {
          isResolved
          isOutdated
          comments(first: 20) {
            nodes {
              databaseId
              body
              path
              line
              author { login }
            }
          }
        }
      }
    }
  }
}
"""


async def get_unresolved_review_threads(
    workspace_path: str,
    target_repo: Optional[str],
    pr_number: Optional[int],
    push_identity: Optional[PushIdentity] = None
) -> List[Dict[str, Any]]:
    """Returns the comments of unresolved inline review threads.

    Inline comments — the usual way to ask for a change, written on the line
    itself — do not appear in `gh pr view --json comments` at all, so a review
    made entirely of them looked like silence. Only the REST/GraphQL review
    thread API exposes them along with their resolution state.

    Resolved and outdated threads are dropped: a conversation the reviewer has
    already closed must not be sent back to the worker a second time.

    Each entry has the same shape as an issue comment (`author`, `body`,
    `databaseId`) so callers can treat both alike, with the file and line
    prefixed onto the body so the worker knows where to look.
    """
    import json

    if not pr_number or not target_repo or "/" not in str(target_repo):
        return []

    owner, _, repo = str(target_repo).partition("/")
    code, out, _ = await run_cmd_async(
        [
            "gh", "api", "graphql",
            "-f", f"query={_REVIEW_THREADS_QUERY}",
            "-F", f"owner={owner}",
            "-F", f"repo={repo}",
            "-F", f"number={int(pr_number)}",
        ],
        cwd=workspace_path,
        timeout=20.0,
        env=_with_identity_env(push_identity)
    )
    if code != 0 or not out.strip():
        return []

    try:
        payload = json.loads(out)
        threads = (
            payload["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
        )
    except Exception:
        return []

    collected: List[Dict[str, Any]] = []
    for thread in threads or []:
        if not isinstance(thread, dict) or thread.get("isResolved") or thread.get("isOutdated"):
            continue
        for comment in (thread.get("comments") or {}).get("nodes") or []:
            body = (comment.get("body") or "").strip()
            if not body:
                continue
            location = comment.get("path") or ""
            if location:
                line = comment.get("line")
                location = f"{location}:{line}" if line else location
                body = f"[{location}] {body}"
            collected.append({
                "author": comment.get("author") or {},
                "body": body,
                "databaseId": comment.get("databaseId"),
            })

    return collected



async def merge_pull_request(
    workspace_path: str,
    pr_url_or_number: str,
    squash: bool = True,
    delete_branch: bool = True,
    target_repo: Optional[str] = None,
    push_identity: Optional[PushIdentity] = None
) -> Tuple[bool, str, str]:
    """
    Merges a GitHub PR using gh pr merge and returns (success, commit_url, log_or_error).
    """
    resolved_repo = target_repo or await discover_target_repo(workspace_path)
    cmd = ["gh", "pr", "merge", str(pr_url_or_number)]
    if squash:
        cmd.append("--squash")
    if delete_branch:
        cmd.append("--delete-branch")
    if resolved_repo and not str(pr_url_or_number).startswith("http"):
        cmd.extend(["--repo", resolved_repo])

    code, out, err = await run_cmd_async(
        cmd, cwd=workspace_path, timeout=30.0, env=_with_identity_env(push_identity)
    )
    if code == 0:
        # Attempt to discover the merged commit SHA or construct commit URL
        import re
        commit_url = ""
        # Check if merge commit SHA is in output or query gh pr view
        status = await get_pull_request_status(
            workspace_path, pr_url_or_number, target_repo=resolved_repo, push_identity=push_identity
        )
        commit_sha = status.get("mergeCommit", {}).get("oid") if isinstance(status.get("mergeCommit"), dict) else None
        
        pr_url = status.get("url") or str(pr_url_or_number)
        if commit_sha:
            repo_base = pr_url.split("/pull/")[0] if "/pull/" in pr_url else f"https://github.com/{resolved_repo or 'local-repo'}"
            commit_url = f"{repo_base}/commit/{commit_sha}"
        elif "/pull/" in pr_url:
            repo_base = pr_url.split("/pull/")[0]
            commit_url = f"{repo_base}/commit/latest_merged"
        else:
            commit_url = f"{pr_url}#merged"

        return True, commit_url, out or "PR squashed and merged successfully."
    else:
        if simulated_git_allowed() and (
            "not logged in" in (err + out).lower() or "no default repository" in (err + out).lower()
        ):
            return True, f"{pr_url_or_number}/commit/simulated_squash_merge", "Simulated merge in local test environment."
        return False, "", f"gh pr merge failed: {err or out}"


async def teardown_worktree(repo_path: str, workspace_path: str) -> Tuple[bool, str]:
    """Force removes the temporary worktree and prunes git refs."""
    abs_repo = os.path.abspath(repo_path)
    abs_ws = os.path.abspath(workspace_path)

    # Same lock as provisioning: both drive `git worktree`, which contends on
    # `.git/index.lock`.
    async with repo_lock(abs_repo):
        await run_cmd_async(["git", "worktree", "remove", "--force", abs_ws], cwd=abs_repo, timeout=20.0)
        await run_cmd_async(["git", "worktree", "prune"], cwd=abs_repo, timeout=10.0)

    if os.path.exists(abs_ws):
        try:
            shutil.rmtree(abs_ws, ignore_errors=True)
        except Exception:
            pass

    return True, f"Teardown complete for {abs_ws}"


async def close_pull_request(
    workspace_path: str,
    pr_url_or_number: str,
    target_repo: Optional[str] = None,
    push_identity: Optional[PushIdentity] = None,
    comment: str = "Closed by a coding graph reset."
) -> Tuple[bool, str]:
    """Closes a PR without merging. Returns (success, log_or_error).

    A PR that is already closed or merged counts as success: reset has to be
    repeatable, and "it is already in the state you asked for" is not a failure.
    """
    cmd = ["gh", "pr", "close", str(pr_url_or_number), "--comment", comment]
    if target_repo and not str(pr_url_or_number).startswith("http"):
        cmd.extend(["--repo", target_repo])

    code, out, err = await run_cmd_async(
        cmd, cwd=workspace_path, timeout=20.0, env=_with_identity_env(push_identity)
    )
    if code == 0:
        return True, out.strip() or "closed"

    combined = f"{out} {err}".lower()
    if "already closed" in combined or "was merged" in combined or "not found" in combined:
        return True, "already closed"
    return False, (err or out).strip()


async def delete_branch(
    repo_path: str,
    branch_name: str,
    target_repo: Optional[str] = None,
    push_identity: Optional[PushIdentity] = None,
    delete_remote: bool = True
) -> Tuple[bool, str]:
    """Deletes a branch locally and on the remote. Returns (success, log_or_error).

    A branch that does not exist on either side is success, for the same reason
    as above. The local delete is forced: the branch is being discarded, so
    "not fully merged" is the expected state, not a warning to respect.
    """
    abs_repo = os.path.abspath(repo_path)
    problems: List[str] = []

    code, out, err = await run_cmd_async(
        ["git", "branch", "-D", branch_name], cwd=abs_repo, timeout=15.0
    )
    if code != 0 and "not found" not in f"{out}{err}".lower():
        problems.append(f"local: {(err or out).strip()}")

    if delete_remote:
        # Same credential helper as the push that created it, or the delete
        # authenticates as the wrong account and is refused.
        push_cmd = ["git"] + git_credential_args(push_identity) + [
            "push", "origin", "--delete", branch_name
        ]
        code, out, err = await run_cmd_async(
            push_cmd,
            cwd=abs_repo,
            timeout=30.0,
            env=_with_identity_env(push_identity)
        )
        combined = f"{out} {err}".lower()
        if code != 0 and "remote ref does not exist" not in combined and "not found" not in combined:
            problems.append(f"remote: {(err or out).strip()}")

    if problems:
        return False, "; ".join(problems)
    return True, f"deleted {branch_name}"



async def comment_pull_request(
    workspace_path: str,
    pr_url: str,
    body: str,
    target_repo: Optional[str] = None,
    push_identity: Optional[PushIdentity] = None
) -> Tuple[bool, str]:
    """
    Posts a comment to a GitHub Pull Request using gh pr comment.
    Returns (success, log_or_error).
    """
    if not pr_url:
        return False, "Missing pr_url"

    resolved_repo = target_repo or await discover_target_repo(workspace_path)
    cmd = ["gh", "pr", "comment", str(pr_url), "--body", body]
    if resolved_repo and not str(pr_url).startswith("http"):
        cmd.extend(["--repo", resolved_repo])

    code, out, err = await run_cmd_async(
        cmd,
        cwd=workspace_path if os.path.exists(workspace_path) else ".",
        timeout=30.0,
        env=_with_identity_env(push_identity)
    )
    if code == 0:
        return True, out.strip() or "Comment posted successfully."

    combined = (out + " " + err).lower()
    if simulated_git_allowed() and (
        "not logged in" in combined or "no default repository" in combined or "fatal" in combined
    ):
        return True, "Simulated PR comment in local/offline test environment."

    return False, f"gh pr comment failed: {err.strip() or out.strip()}"


async def preflight_push_access(
    target_repo: Optional[str],
    cwd: str = ".",
    push_identity: Optional[PushIdentity] = None
) -> Tuple[bool, str]:
    """Verifies the active credential can actually push to `target_repo`.

    Run before any LLM work: an expired token or a bot that was never added as a
    collaborator then costs zero tokens instead of a full implementation run that
    dies at the push. Returns (ok, message) and never raises.
    """
    if not target_repo:
        target_repo = await discover_target_repo(cwd, ".")
    if not target_repo:
        return False, (
            "Preflight failed: could not determine the target repository "
            "(no `repo.slug` in the manifest and no github.com remote named origin)."
        )

    env = _with_identity_env(push_identity)

    # 1. Who are we? A mismatch here is the difference between the bot opening the
    #    PR (approvable) and the human opening it (not approvable by themselves).
    code, out, err = await run_cmd_async(
        ["gh", "api", "user", "--jq", ".login"], cwd=cwd, timeout=15.0, env=env
    )
    if code != 0:
        detail = (err.strip() or out.strip())
        return False, (
            f"Preflight failed: `gh` is not authenticated for {target_repo} ({detail}). "
            f"Check the machine-user token, or run `gh auth login`."
        )
    login = out.strip()

    if push_identity and login and login.lower() != push_identity.login.lower():
        return False, (
            f"Preflight failed: token authenticates as `{login}` but the manifest "
            f"declares `repo.push_identity: {push_identity.login}`. "
            f"PRs would be opened by the wrong account."
        )

    # 2. Can that account push? `permissions.push` covers both collaborator role
    #    and fine-grained PAT scope, which is exactly the pair that goes wrong.
    code, out, err = await run_cmd_async(
        ["gh", "api", f"repos/{target_repo}", "--jq", ".permissions.push"],
        cwd=cwd, timeout=15.0, env=env
    )
    if code != 0:
        detail = (err.strip() or out.strip())
        return False, (
            f"Preflight failed: `{login}` cannot read {target_repo} ({detail}). "
            f"Add the account as a collaborator and grant the token Metadata: Read."
        )

    if out.strip().lower() != "true":
        return False, (
            f"Preflight failed: `{login}` has no push permission on {target_repo}. "
            f"Grant Write access to the account and Contents: Read and write to its token."
        )

    return True, f"Preflight OK: `{login}` can push to {target_repo}."


async def ensure_label(
    target_repo: str,
    name: str = "approved",
    cwd: str = ".",
    description: str = "Approved by the reviewer: the coding graph may merge this PR.",
    color: str = "0E8A16",
    push_identity: Optional[PushIdentity] = None
) -> Tuple[bool, str]:
    """Creates a repo label if it does not exist. Idempotent."""
    cmd = ["gh", "label", "create", name, "--repo", target_repo,
           "--description", description, "--color", color]
    code, out, err = await run_cmd_async(
        cmd, cwd=cwd, timeout=15.0, env=_with_identity_env(push_identity)
    )
    combined = (out + " " + err)
    if code == 0:
        return True, f"Created label `{name}` on {target_repo}."
    if "already exists" in combined.lower():
        return True, f"Label `{name}` already exists on {target_repo}."
    return False, f"gh label create failed: {err.strip() or out.strip()}"


