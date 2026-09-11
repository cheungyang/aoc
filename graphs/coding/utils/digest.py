"""Content digest of a worktree.

The tick model promises never to redo the LLM part after an infrastructure
failure. That promise needs evidence, not a flag: a boolean "implemented"
would still be true after someone reset the branch or the worktree was wiped.

The digest is taken over what the worker actually produced — tracked changes
plus untracked files — so:

- `implement` skips when the recorded digest still matches (the code is there);
- `verify` reuses its previous result only for the exact tree it tested;
- resetting the branch or losing the worktree changes the digest, and the work
  is honestly redone.
"""
import hashlib
from typing import Optional

from core.util import git_ops


async def compute_worktree_digest(workspace_path: str) -> Optional[str]:
    """Returns a stable hash of the worktree's current changes, or None if unknown.

    None means "no evidence" (missing worktree, git failure). Callers must treat
    that as a cache miss and redo the work rather than assuming a match.
    """
    if not workspace_path:
        return None

    # Tracked modifications, staged or not, relative to the branch head.
    code, diff_out, _ = await git_ops.run_cmd_async(
        ["git", "diff", "HEAD"], cwd=workspace_path, timeout=15.0
    )
    if code != 0:
        return None

    # Untracked files are invisible to `git diff` but are most of what a fresh
    # implementation produces, so their names and contents count too.
    code, status_out, _ = await git_ops.run_cmd_async(
        ["git", "status", "--porcelain"], cwd=workspace_path, timeout=15.0
    )
    if code != 0:
        return None

    digest = hashlib.sha256()
    digest.update(diff_out.encode("utf-8", errors="replace"))
    digest.update(b"\0")
    # Sorted so the hash does not depend on git's listing order.
    digest.update("\n".join(sorted(status_out.splitlines())).encode("utf-8", errors="replace"))

    return digest.hexdigest()


async def digest_matches(workspace_path: str, expected: Optional[str]) -> bool:
    """True only when a digest could be computed and equals `expected`."""
    if not expected:
        return False
    actual = await compute_worktree_digest(workspace_path)
    return bool(actual) and actual == expected
