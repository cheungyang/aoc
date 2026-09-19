"""HTTP access to Home Assistant.

Two rules shape this module, mirroring `core.util.push_identity`:

1. The token never enters the repository's history, the environment, a log line,
   a URL or an exception message. It is read from an untracked file that both git
   and the Docker build context ignore, held on the client instance, and sent only
   in an Authorization header. Every string this module raises or returns passes
   through `redact()` first.
2. A misconfigured credential fails loudly and early, with a message that says how
   to fix it -- an expired token should cost zero LLM tokens, not a whole run.

Why a module and not a tool: the agent-facing surface is a single `home_assistant`
tool (see the integration plan). This layer knows about HTTP, retries and secrets;
it knows nothing about actions, permissions or agents. That split is what lets the
guard layer in Phase 3 sit between them and be impossible to route around.

Scope note: this is the REST half. The WebSocket half (entity/device/area
registries) arrives in Phase 2 as a sibling module -- the registries are the only
thing REST cannot reach, and they need a different transport.
"""
import os
import re
import time
from typing import Any, Dict, Optional

import requests

# Importing Config is what loads .env (it calls load_dotenv at import time), so
# every environment read below goes through it rather than os.environ directly.
# Reading os.environ straight would work inside the running app -- something else
# always imports Config first -- but would fail for any script, probe or test that
# imports this module on its own, which is a trap worth closing here.
from core.util.config import Config

# The token sits at the project root: one file serves both the host and, through
# the `.:/app` bind mount, the container at /app/home_assistant_token. Living
# inside the working tree is only safe because it is excluded from git
# (.gitignore) and the image build context (.dockerignore); keep both entries if
# you move this file. Derived from __file__ rather than the process CWD, which
# may be a worktree under WORKSPACES_DIR.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DEFAULT_TOKEN_PATH = os.path.join(PROJECT_ROOT, "home_assistant_token")

TOKEN_PATH_ENV_VAR = "HA_TOKEN_FILE"
BASE_URL_ENV_VAR = "HA_BASE_URL"
TIMEOUT_ENV_VAR = "HA_REQUEST_TIMEOUT"
WRITE_ENABLED_ENV_VAR = "HA_WRITE_ENABLED"

DEFAULT_TIMEOUT = 20

# Retried once per attempt with linear backoff. Deliberately excludes 4xx: a 401
# is a bad token and a 404 is a bad path, and retrying either just delays an
# error the caller needs to see.
RETRY_STATUS = frozenset({429, 502, 503, 504})
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 0.5

REDACTED = "***REDACTED***"

# Catches a credential echoed back inside a library's own exception text, even
# when it is not the token this client holds (a stale client, a second instance).
_BEARER_RE = re.compile(r"Bearer\s+\S+")


class HomeAssistantError(Exception):
    """Raised when Home Assistant cannot be reached or refuses a request."""


def resolve_token_path(token_path: Optional[str] = None) -> str:
    """Resolves the credential file path: argument, then env var, then default."""
    raw = token_path or Config().get(TOKEN_PATH_ENV_VAR) or DEFAULT_TOKEN_PATH
    return os.path.abspath(os.path.expanduser(raw))


def read_token(token_path: Optional[str] = None) -> str:
    """Reads the long-lived access token, refusing a file other users can read.

    Raises HomeAssistantError with an actionable message rather than returning an
    empty string: a silently empty token would produce a 401 several layers later,
    which is a much harder thing to diagnose than a missing file.
    """
    path = resolve_token_path(token_path)

    if not os.path.exists(path):
        raise HomeAssistantError(
            f"Home Assistant token file not found at {path}. "
            f"Create it with: install -m 600 /dev/null {path} && "
            f"pbpaste > {path}  (or set {TOKEN_PATH_ENV_VAR})."
        )

    mode = os.stat(path).st_mode
    if mode & 0o077:
        raise HomeAssistantError(
            f"Home Assistant token file {path} is readable by other users "
            f"(mode {oct(mode & 0o777)}). Run: chmod 600 {path}"
        )

    with open(path, "r", encoding="utf-8") as handle:
        token = handle.read().strip()

    if not token:
        raise HomeAssistantError(f"Home Assistant token file {path} is empty.")

    return token


def redact(text: Any, token: Optional[str] = None) -> str:
    """Removes the token from a string before it is logged, raised or returned.

    Also strips any `Bearer <something>` sequence, which catches the case where a
    library formats the request headers into its own exception message and the
    token there is not the one we hold (a stale client, a second instance).
    """
    if text is None:
        return ""
    out = str(text)
    if token:
        out = out.replace(token, REDACTED)
    return _BEARER_RE.sub(f"Bearer {REDACTED}", out)


def write_enabled() -> bool:
    """The master kill-switch for every mutating action.

    Read here rather than in the guard layer so that "can this process write at
    all?" has exactly one answer, available before any action is dispatched.
    Enforcement lands with the guards in Phase 3; nothing in this module mutates.
    """
    return str(Config().get(WRITE_ENABLED_ENV_VAR, "")).strip().lower() in {"1", "true", "yes", "on"}


class HomeAssistantClient:
    """A thin, synchronous REST client for one Home Assistant instance.

    Synchronous on purpose: the tools in this repo are plain `def` (see
    `tools/filesystem.py`), and LangChain runs them in a worker thread. An async
    client here would mean managing an event loop inside a sync tool for no gain.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: Optional[int] = None,
        session: Optional[requests.Session] = None,
        token_path: Optional[str] = None,
    ):
        resolved_base = (base_url or Config().get(BASE_URL_ENV_VAR) or "").strip()
        if not resolved_base:
            raise HomeAssistantError(
                f"{BASE_URL_ENV_VAR} is not set. Add it to .env, e.g. "
                f"{BASE_URL_ENV_VAR}=https://homeassistant.example.com"
            )
        # Trailing slashes would produce '//api/...' which some reverse proxies
        # (including Synology's) treat as a distinct, unmapped path.
        self.base_url = resolved_base.rstrip("/")

        # Stored private and never exposed on the instance's public surface, so a
        # logged/repr'd client cannot leak it.
        self._token = token if token is not None else read_token(token_path)

        self.timeout = int(timeout if timeout is not None else Config().get(TIMEOUT_ENV_VAR, DEFAULT_TIMEOUT))
        self._session = session or requests.Session()

    def __repr__(self) -> str:
        """Loggable. Never includes the token."""
        return f"<HomeAssistantClient {self.base_url} timeout={self.timeout}s>"

    def describe(self) -> str:
        """Loggable one-liner for startup diagnostics. Never includes the token."""
        return (
            f"home assistant: {self.base_url} "
            f"(token from {resolve_token_path()}, writes "
            f"{'enabled' if write_enabled() else 'disabled'})"
        )

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def request(
        self,
        method: str,
        path: str,
        json: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Performs one request, retrying only what is worth retrying.

        Returns parsed JSON when the response carries it, otherwise the body text.
        Raises HomeAssistantError -- already redacted -- on any failure.
        """
        url = self._url(path)
        last_error = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self._session.request(
                    method=method.upper(),
                    url=url,
                    headers=self._headers(),
                    json=json,
                    params=params,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                # Connection-level failure: the NAS rebooting, DNS blipping, the
                # tunnel dropping. Worth another attempt.
                last_error = HomeAssistantError(
                    f"Could not reach Home Assistant at {url}: {redact(exc, self._token)}"
                )
                if attempt < MAX_ATTEMPTS:
                    time.sleep(BACKOFF_SECONDS * attempt)
                    continue
                raise last_error from None

            if response.status_code in RETRY_STATUS and attempt < MAX_ATTEMPTS:
                time.sleep(BACKOFF_SECONDS * attempt)
                continue

            if response.status_code == 401:
                raise HomeAssistantError(
                    f"Home Assistant rejected the token (401) for {url}. "
                    f"The token in {resolve_token_path()} may be expired or revoked; "
                    f"issue a new one under Profile > Security."
                )

            if response.status_code == 403:
                raise HomeAssistantError(
                    f"Home Assistant refused the request (403) for {url}. "
                    f"The token's user may not be an administrator."
                )

            if response.status_code >= 400:
                raise HomeAssistantError(
                    f"Home Assistant returned {response.status_code} for {method.upper()} {url}: "
                    f"{redact(getattr(response, 'text', ''), self._token)[:500]}"
                )

            return self._parse(response)

        # Only reachable when every attempt hit a retryable status.
        raise last_error or HomeAssistantError(
            f"Home Assistant did not respond successfully to {method.upper()} {url} "
            f"after {MAX_ATTEMPTS} attempts."
        )

    def _parse(self, response) -> Any:
        """JSON when it is JSON, text otherwise.

        `GET /api/` returns JSON, but `GET /api/error_log` returns plain text, and
        both are things we call. Content-Type is not always set the way you would
        hope, so this tries and falls back rather than trusting the header.
        """
        try:
            return response.json()
        except ValueError:
            return getattr(response, "text", "")

    # -- Convenience wrappers ------------------------------------------------

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, json: Optional[Any] = None) -> Any:
        return self.request("POST", path, json=json)

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path)

    def ping(self) -> str:
        """Verifies credentials and reachability in one call.

        Returns the API's own greeting ('API running.'). Used by preflight so a
        bad token surfaces before any model call.
        """
        result = self.get("/api/")
        if isinstance(result, dict):
            return result.get("message", "")
        return str(result)
