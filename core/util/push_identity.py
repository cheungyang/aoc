"""Machine-user credentials for git/GitHub operations.

The coding graph pushes branches and opens PRs as a dedicated GitHub account (a
"machine user") rather than as the human reviewer. That is what makes the native
Approve button available on those PRs: GitHub does not let you formally approve
your own pull request.

Two rules shape this module:

1. The token never enters the repository's history, the manifest, the graph
   state, a log line or a remote URL. It is read from an untracked file that
   both git and the Docker build context ignore, and handed to individual
   subprocesses through their environment, for the lifetime of that subprocess
   only.
2. A misconfigured credential fails loudly and early. Preflight runs before any
   LLM work, so an expired token costs zero tokens rather than a whole run.
"""
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# The token sits at the project root: that single location is the same file on
# the host and, through the `.:/app` bind mount, inside the container at
# /app/github_bot_token -- so one credential serves both without a home
# directory that the container does not share. Living inside the working tree
# is only safe because it is excluded from git (.gitignore) and from the image
# build context (.dockerignore); keep both entries if you move this file.
# Derived from __file__ rather than the process CWD, which is a worktree under
# WORKSPACES_DIR while the coding graph runs.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_TOKEN_PATH = os.path.join(PROJECT_ROOT, "github_bot_token")

# Override for tests and for anyone who keeps credentials elsewhere.
TOKEN_PATH_ENV_VAR = "AOC_BOT_TOKEN_FILE"

# `gh` prefers GH_TOKEN over the credentials stored by `gh auth login`, so
# setting it per subprocess leaves the human's interactive session untouched.
TOKEN_ENV_VAR = "GH_TOKEN"


class PushIdentityError(Exception):
    """Raised when a configured machine user cannot be used as configured."""


@dataclass(frozen=True)
class PushIdentity:
    """A GitHub account the coding graph acts as, plus its credential."""

    login: str
    email: str
    # repr/compare disabled so the token cannot leak through a stack trace, a
    # logged dataclass or an assertion message.
    token: str = field(repr=False, compare=False, default="")
    token_path: str = ""

    @property
    def author(self) -> str:
        """Git author string, e.g. ``cheungyang-bot <...@users.noreply.github.com>``."""
        return f"{self.login} <{self.email}>"


def default_email_for(login: str) -> str:
    """GitHub's no-reply address for an account, so commits attribute correctly."""
    return f"{login}@users.noreply.github.com"


def resolve_token_path(token_path: Optional[str] = None) -> str:
    """Resolves the credential file path: argument, then env var, then default."""
    raw = token_path or os.environ.get(TOKEN_PATH_ENV_VAR) or DEFAULT_TOKEN_PATH
    return os.path.abspath(os.path.expanduser(raw))


def read_token(token_path: Optional[str] = None) -> str:
    """Reads the machine-user token, refusing a file other users can read.

    Raises PushIdentityError with an actionable message instead of returning an
    empty string: a silently empty token would fall back to the human's own
    `gh` credentials, which is precisely the confusion this module exists to
    prevent.
    """
    path = resolve_token_path(token_path)

    if not os.path.exists(path):
        raise PushIdentityError(
            f"Machine-user token file not found at {path}. "
            f"Create it with: install -m 600 /dev/null {path} && "
            f"pbpaste > {path}  (or set {TOKEN_PATH_ENV_VAR})."
        )

    mode = os.stat(path).st_mode
    if mode & 0o077:
        raise PushIdentityError(
            f"Machine-user token file {path} is readable by other users "
            f"(mode {oct(mode & 0o777)}). Run: chmod 600 {path}"
        )

    with open(path, "r", encoding="utf-8") as f:
        token = f.read().strip()

    if not token:
        raise PushIdentityError(f"Machine-user token file {path} is empty.")

    return token


def load_push_identity(
    login: str,
    email: Optional[str] = None,
    token_path: Optional[str] = None
) -> PushIdentity:
    """Builds a PushIdentity for `login`, reading its token from disk.

    Raises PushIdentityError if the login is missing or the token is unusable.
    """
    if not login or not str(login).strip():
        raise PushIdentityError("Push identity requires a GitHub login.")

    clean_login = str(login).strip()
    return PushIdentity(
        login=clean_login,
        email=(email or "").strip() or default_email_for(clean_login),
        token=read_token(token_path),
        token_path=resolve_token_path(token_path)
    )


def subprocess_env(identity: Optional[PushIdentity]) -> Dict[str, str]:
    """Environment overrides that authenticate one git/gh subprocess."""
    if not identity or not identity.token:
        return {}
    return {TOKEN_ENV_VAR: identity.token}


def git_credential_args(identity: Optional[PushIdentity]) -> List[str]:
    """`git -c` arguments that authenticate an HTTPS push as the machine user.

    The helper reads the token from the environment at run time, so the token
    itself never appears in the process arguments (visible via `ps`) nor in
    `.git/config` (which lives inside every worktree).
    """
    if not identity or not identity.token:
        return []
    helper = (
        "!f() { "
        "echo username=x-access-token; "
        f"echo password=${TOKEN_ENV_VAR}; "
        "}; f"
    )
    # The empty value first clears helpers configured globally or by the repo,
    # so the machine user's credential cannot be shadowed by the human's.
    return ["-c", "credential.helper=", "-c", f"credential.helper={helper}"]


def git_commit_args(identity: Optional[PushIdentity]) -> List[str]:
    """`git -c` arguments that attribute a commit to the machine user."""
    if not identity:
        return []
    return ["-c", f"user.name={identity.login}", "-c", f"user.email={identity.email}"]


def describe(identity: Optional[PushIdentity]) -> str:
    """Loggable one-liner. Never includes the token."""
    if not identity:
        return "push identity: none (using ambient gh/git credentials)"
    return f"push identity: {identity.login} <{identity.email}> (token from {identity.token_path})"
