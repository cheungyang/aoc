"""Picks the verification backend.

One switch, `CODING_VERIFY_BACKEND`, defaulting to `e2b`: the code under test
was written by an LLM, so by default it runs in a throwaway sandbox and never
on the host the bot and its credentials live on. A per-project override from
the manifest's `environment.backend` takes precedence, so `local` is an
explicit opt-out — for one project whose tests can't run in a sandbox yet, or
for a whole host via the env var.

An unknown backend is a configuration error and raises: silently falling back
to `local` would run untrusted code on the host precisely when someone asked
for it not to be. A host where E2B isn't set up halts the task with a
configuration error too (`scripts/coding_e2b_check.py` says what is missing),
rather than quietly testing on the host.
"""
import os
from typing import Any, Dict, Optional

from graphs.coding.utils.sandbox.local_runner import LocalRunner
from graphs.coding.utils.sandbox.runner_types import VerifyRunner

BACKEND_ENV_VAR = "CODING_VERIFY_BACKEND"
DEFAULT_BACKEND = "e2b"


class UnknownBackendError(ValueError):
    pass


def resolve_backend(environment: Optional[Dict[str, Any]] = None) -> str:
    """Manifest override first, then the env var, then the default."""
    override = (environment or {}).get("backend")
    value = override or os.environ.get(BACKEND_ENV_VAR) or DEFAULT_BACKEND
    return str(value).strip().lower()


def get_runner(environment: Optional[Dict[str, Any]] = None) -> VerifyRunner:
    backend = resolve_backend(environment)
    if backend == "local":
        return LocalRunner()
    if backend == "e2b":
        from graphs.coding.utils.sandbox.e2b_runner import E2BRunner

        return E2BRunner(template=(environment or {}).get("template") or None)
    raise UnknownBackendError(
        f"Unknown verify backend `{backend}` (from manifest `environment.backend` "
        f"or ${BACKEND_ENV_VAR}). Supported: local, e2b."
    )
