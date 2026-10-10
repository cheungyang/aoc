"""Picks the verification backend.

One switch, `CODING_VERIFY_BACKEND`, defaulting to `local` so that merging the
runner abstraction changes nothing. A per-project override from the manifest's
`environment.backend` takes precedence, so a single project can be moved to a
remote backend without flipping the whole system.

An unknown backend is a configuration error and raises: silently falling back
to `local` would run untrusted code on the host precisely when someone asked
for it not to be.
"""
import os
from typing import Any, Dict, Optional

from graphs.coding.utils.sandbox.local_runner import LocalRunner
from graphs.coding.utils.sandbox.runner_types import VerifyRunner

BACKEND_ENV_VAR = "CODING_VERIFY_BACKEND"
DEFAULT_BACKEND = "local"


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
