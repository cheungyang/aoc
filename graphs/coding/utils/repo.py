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
