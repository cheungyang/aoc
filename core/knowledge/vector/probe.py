"""Runtime capability probe for native dependencies.

LanceDB's x86_64 wheels assume an AVX2-era CPU. On hardware without it the
import does not raise -- it executes an illegal instruction and the kernel kills
the process. That makes the obvious check useless:

    try:
        import lancedb          # SIGILL: no exception, no traceback, no process
    except ImportError:
        ...                     # never reached

So the import is attempted in a child process instead. The child dies; the
parent reads its exit status and lives. Exit 132 is 128 + SIGILL(4).

The verdict is memoized for the life of the process and deliberately not cached
to disk: it costs about a second at startup, runs once per process rather than
per query, and a stale cache -- after moving the volume to different hardware,
say -- would be acted on with confidence and crash the bot.
"""

import subprocess
import sys

PROBE_TIMEOUT_SECONDS = 60

_cache = {}


def _run_import_probe(module: str) -> bool:
    try:
        result = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            capture_output=True,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
        return result.returncode == 0
    except Exception:
        # A probe we cannot run is not evidence the module works.
        return False


def can_import(module: str, use_cache: bool = True) -> bool:
    """True if `module` imports in a child process without crashing it."""
    if use_cache and module in _cache:
        return _cache[module]

    ok = _run_import_probe(module)
    _cache[module] = ok
    return ok


def lancedb_usable(use_cache: bool = True) -> bool:
    return can_import("lancedb", use_cache=use_cache)


def reset_cache() -> None:
    """Clears memoized verdicts. For tests."""
    _cache.clear()
