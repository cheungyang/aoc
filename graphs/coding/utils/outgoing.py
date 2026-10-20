"""What a push would send, checked for personal data before it leaves.

The push node calls this right before it commits and pushes. A task branch is
public the moment it lands on GitHub, and verification only comes after, so
this is the last point where a line copied from the user's notes can still be
stopped. In order:

1. **What GitHub already has.** The task branch if an earlier attempt pushed
   it, else the commit the branch left the default branch at. That is public
   already, so only what differs from it is checked.
2. **Fold.** Commits that never reached GitHub go back into the worktree,
   staged. The push then sends one commit holding exactly the files checked
   here. Without this, a commit from a failed push, or from before this check
   existed, would go out underneath a later fix.
3. **Check** every file that differs from GitHub, whether committed, staged,
   unstaged or new, with both privacy checks. Ignored files are never pushed,
   so they are not read; deleted ones have nothing left to read.

Git runs synchronously, in a worker thread when called from the graph.
"""
import asyncio
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from core.util import privacy_guard
from core.util.privacy_guard import Hit


class GitFailed(Exception):
    """git could not say what a push would send."""


@dataclass
class OutgoingCheck:
    hits: List[Hit] = field(default_factory=list)
    # Set when git could not list what would be sent. Nothing was checked, so
    # nothing may be pushed.
    error: str = ""
    files: List[str] = field(default_factory=list)


def _git(workspace: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", workspace, *args],
        capture_output=True, stdin=subprocess.DEVNULL, timeout=60,
    )


def _out(proc: subprocess.CompletedProcess) -> str:
    return proc.stdout.decode("utf-8", "surrogateescape").strip()


def _failed(proc: subprocess.CompletedProcess, what: str) -> GitFailed:
    detail = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()
    return GitFailed(f"{what} failed: {detail or f'exit {proc.returncode}'}")


def public_base(workspace: str, branch: str, default_branch: str) -> str:
    """The commit GitHub already has for this task, or "" if there is none to compare with."""
    proc = _git(workspace, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}")
    if proc.returncode == 0 and _out(proc):
        return _out(proc)
    default = (default_branch or "main").replace("origin/", "", 1)
    proc = _git(workspace, "merge-base", "HEAD", f"refs/remotes/origin/{default}")
    return _out(proc) if proc.returncode == 0 else ""


def fold_unpushed(workspace: str, base: str) -> None:
    """Moves HEAD back to `base`, keeping every change staged.

    Only when HEAD is ahead of `base` on the same line of history. A branch that
    has diverged from GitHub is left alone: its push is refused as
    non-fast-forward, so nothing can leave through it.
    """
    if not base:
        return
    proc = _git(workspace, "rev-parse", "HEAD")
    if proc.returncode != 0:
        raise _failed(proc, "git rev-parse HEAD")
    if _out(proc) == base:
        return
    proc = _git(workspace, "merge-base", "--is-ancestor", base, "HEAD")
    if proc.returncode == 1:
        return
    if proc.returncode != 0:
        raise _failed(proc, "git merge-base --is-ancestor")
    proc = _git(workspace, "reset", "--soft", base)
    if proc.returncode != 0:
        raise _failed(proc, "git reset --soft")


def outgoing_files(workspace: str, base: str) -> List[str]:
    """The files whose content GitHub doesn't have yet.

    With no base to compare with, every file git would push, like the test.
    """
    if base:
        commands = [
            ("git diff", ["diff", "--name-only", "--no-renames", "--diff-filter=d", "-z", base]),
            ("git ls-files", ["ls-files", "-z", "--others", "--exclude-standard"]),
        ]
    else:
        commands = [("git ls-files", ["ls-files", "-z", "--cached", "--others", "--exclude-standard"])]
    paths = set()
    for what, args in commands:
        proc = _git(workspace, *args)
        if proc.returncode != 0:
            raise _failed(proc, what)
        paths.update(p for p in proc.stdout.decode("utf-8", "surrogateescape").split("\0") if p)
    return sorted(paths)


def require_task_branch(workspace: str, branch: str) -> None:
    """Refuses unless the worktree is on the task branch.

    The fold moves HEAD. Pointed at any other checkout, such as a local main
    with commits not yet pushed, it would rewrite that branch, so a mismatch is
    an error and nothing is folded or checked.
    """
    proc = _git(workspace, "symbolic-ref", "--quiet", "--short", "HEAD")
    current = _out(proc) if proc.returncode == 0 else ""
    if current != branch:
        raise GitFailed(f"the worktree is on `{current or 'a detached HEAD'}`, not the task branch `{branch}`")


def check_outgoing_sync(workspace: str, branch: str, default_branch: str, pkm: Path) -> OutgoingCheck:
    """Folds unpushed commits, then runs both privacy checks on what the push would send.

    Raises privacy_guard.NotesMissing before touching git when the notes can't
    be found: "couldn't look" must never read as "clean".
    """
    if not privacy_guard.has_notes(pkm):
        raise privacy_guard.NotesMissing(
            f"no personal notes under {pkm} ({', '.join(privacy_guard.PERSONAL_DIRS)})"
        )
    try:
        require_task_branch(workspace, branch)
        base = public_base(workspace, branch, default_branch)
        fold_unpushed(workspace, base)
        paths = outgoing_files(workspace, base)
    except (GitFailed, OSError, subprocess.SubprocessError) as e:
        return OutgoingCheck(error=str(e))
    paths = [path for path in paths if path != privacy_guard.SELF]
    texts = privacy_guard.pushed_texts(Path(workspace), paths)
    return OutgoingCheck(hits=privacy_guard.check(texts, pkm), files=paths)


async def check_outgoing(workspace: str, branch: str, default_branch: str, pkm: Path) -> OutgoingCheck:
    """`check_outgoing_sync` off the event loop: reading the notes takes about half a second."""
    return await asyncio.to_thread(check_outgoing_sync, workspace, branch, default_branch, pkm)
