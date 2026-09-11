"""Repository descriptor and machine-user resolution for the coding graph.

The manifest is the system of record, so the repository the graph works against
and the account it pushes as are both declared there:

```jsonc
"repo": {
  "mode": "self",                     // "self" | "existing" | "create"
  "slug": "cheungyang/aoc",
  "default_branch": "main",
  "visibility": "private",
  "push_identity": "cheungyang-bot"   // omit to use ambient git/gh credentials
}
```

The credential itself is deliberately *not* here: `push_identity` names the
account, and the token is read from a file outside the repository. That keeps the
manifest safe to commit and means the token never enters graph state or a
checkpoint.
"""
import os
from typing import Any, Dict, Optional, Tuple

from core.util.push_identity import PushIdentity, PushIdentityError, load_push_identity

DEFAULT_REPO_DESCRIPTOR: Dict[str, Any] = {
    "mode": "self",
    "slug": None,
    "default_branch": "main",
    "visibility": "private",
    "push_identity": None,
    "push_identity_email": None,
}


def get_repo_descriptor(source: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Reads the `repo` block out of a manifest or graph state, with defaults filled in."""
    descriptor = dict(DEFAULT_REPO_DESCRIPTOR)
    if not source:
        return descriptor

    raw = source.get("repo")
    if isinstance(raw, dict):
        descriptor.update({k: v for k, v in raw.items() if v is not None})

    # A bare `target_repo` at the top level predates the repo block; honour it so
    # existing manifests keep working.
    if not descriptor.get("slug") and source.get("target_repo"):
        descriptor["slug"] = source["target_repo"]

    return descriptor


def resolve_push_identity(
    descriptor: Optional[Dict[str, Any]]
) -> Tuple[Optional[PushIdentity], str]:
    """Loads the machine user named by the descriptor.

    Returns (identity, error):
    - (None, "")      no machine user configured — ambient git/gh credentials are used
    - (None, reason)  one is configured but unusable; the caller must halt rather than
                      quietly fall back to the human's credentials and open a PR the
                      human cannot approve
    - (identity, "")  ready to use
    """
    descriptor = descriptor or {}
    login = descriptor.get("push_identity")
    if not login:
        return None, ""

    try:
        identity = load_push_identity(
            login=login,
            email=descriptor.get("push_identity_email")
        )
    except PushIdentityError as e:
        return None, str(e)

    return identity, ""


def get_push_identity(state: Optional[Dict[str, Any]]) -> Optional[PushIdentity]:
    """Best-effort identity lookup for a node that already passed preflight.

    Preflight (in the provisioner) is what turns a broken credential into a halt;
    by the time downstream nodes run, a failure here means the token file changed
    mid-run, so the node proceeds with ambient credentials and the push fails
    honestly rather than the node crashing.
    """
    identity, _ = resolve_push_identity(get_repo_descriptor(state))
    return identity


# --- Repository modes (§9) -------------------------------------------------
#
# self      the repo this process runs from; worktrees under workspaces/runs/
# existing  a managed clone at workspaces/repos/<owner>__<repo>, reused
# create    `gh repo create`, seed, then behave exactly as `existing`

REPO_CACHE_DIR = os.path.join("workspaces", "repos")


def project_root() -> str:
    """The repository root, from this file's location — never `os.getcwd()`.

    Concurrent ticks cannot share a working directory (§8.4), and a scheduled
    run's cwd is whatever the scheduler chose, so deriving the root from the
    process's cwd made the answer depend on who was asking.

    Walking up to a marker rather than counting `dirname` calls: the count was
    wrong once already (it stopped at `graphs/`) and moving this file would
    break it again silently.
    """
    path = os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.isdir(os.path.join(path, ".git")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            # No marker found — fall back to the known nesting of this file:
            # graphs/coding/utils/repo.py is four levels below the root.
            return os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))))
        path = parent


def cache_path_for(slug: str) -> str:
    """Where the managed clone of `owner/repo` lives.

    Anchored to the project root, not the cwd: the cache must be the same
    directory for every tick, or `existing` would re-clone per caller.

    Flattened with `__` so every clone is one directory deep, which keeps
    `workspaces/repos` listable and avoids an empty `owner/` left behind when a
    clone is removed.
    """
    owner, _, name = str(slug).partition("/")
    return os.path.join(project_root(), REPO_CACHE_DIR, f"{owner}__{name}")


def resolve_repo_root(descriptor: Dict[str, Any]) -> str:
    """Where this repo's checkout *would* be, without touching the network.

    Cleanup paths need the same answer as the scheduler, but a reset must never
    clone a repository just to work out which branch to delete — so this is the
    pure function and `ensure_repo_available` is the one that does the work.
    Returns "" when the descriptor cannot name a location.
    """
    mode = (descriptor.get("mode") or "self").lower()
    if mode == "self":
        return project_root()
    slug = descriptor.get("slug")
    if not slug or "/" not in str(slug):
        return ""
    return cache_path_for(str(slug))


async def ensure_repo_available(
    descriptor: Dict[str, Any],
    push_identity: Optional[PushIdentity] = None
) -> Tuple[str, str]:
    """Makes the target repository present locally. Returns (repo_root, error).

    Idempotent by design: a clone that already exists is fetched, not
    re-cloned, and `create` skips creation when the repository is already
    there. A tick may call this every five minutes.
    """
    mode = (descriptor.get("mode") or "self").lower()

    if mode == "self":
        return project_root(), ""

    if mode not in ("existing", "create"):
        return "", f"Unknown repo.mode `{mode}`. Use `self`, `existing` or `create`."

    slug = descriptor.get("slug")
    if not slug or "/" not in str(slug):
        return "", f"repo.mode is `{mode}` but repo.slug is missing or not `owner/repo` (got {slug!r})."

    if mode == "create":
        created, message = await _ensure_remote_exists(descriptor, push_identity)
        if not created:
            return "", message

    return await _ensure_clone(slug, push_identity)


async def _ensure_remote_exists(
    descriptor: Dict[str, Any],
    push_identity: Optional[PushIdentity]
) -> Tuple[bool, str]:
    """Creates the GitHub repository if it is not there yet."""
    from core.util import git_ops
    from core.util.push_identity import subprocess_env

    slug = descriptor["slug"]
    env = subprocess_env(push_identity) or None

    code, _, _ = await git_ops.run_cmd_async(
        ["gh", "repo", "view", slug, "--json", "name"], cwd=".", timeout=20.0, env=env
    )
    if code == 0:
        return True, ""

    visibility = (descriptor.get("visibility") or "private").lower()
    cmd = ["gh", "repo", "create", slug, f"--{visibility}"]
    if descriptor.get("template"):
        cmd.extend(["--template", str(descriptor["template"])])
    else:
        # Without an initial commit there is no default branch to base work on,
        # and every worktree would fail to resolve its base ref.
        cmd.append("--add-readme")

    code, out, err = await git_ops.run_cmd_async(cmd, cwd=".", timeout=60.0, env=env)
    if code != 0:
        return False, f"Could not create `{slug}`: {(err or out).strip()}"
    return True, ""


async def _ensure_clone(
    slug: str,
    push_identity: Optional[PushIdentity]
) -> Tuple[str, str]:
    """Clones the repo into the cache if needed, then fetches. Returns (path, error)."""
    from core.util import git_ops
    from core.util.push_identity import git_credential_args, subprocess_env

    target = cache_path_for(slug)
    env = subprocess_env(push_identity) or None
    credentials = git_credential_args(push_identity)

    if not os.path.isdir(os.path.join(target, ".git")):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        code, out, err = await git_ops.run_cmd_async(
            ["git"] + credentials + ["clone", f"https://github.com/{slug}.git", target],
            cwd=".", timeout=300.0, env=env
        )
        if code != 0:
            return "", f"Could not clone `{slug}`: {(err or out).strip()}"
        return target, ""

    # Serialized with the worktree operations: a fetch takes the same index lock.
    # A fetch that fails is not fatal — the clone is still usable, the base ref
    # is just a few commits behind — so the error is swallowed deliberately.
    async with git_ops.repo_lock(target):
        await git_ops.run_cmd_async(
            ["git"] + credentials + ["fetch", "origin", "--prune"],
            cwd=target, timeout=120.0, env=env
        )

    return target, ""


