"""Test-suite-wide isolation from production state.

Several stores hard-default to the real `sessions/memory.db` (JobManager,
SqliteSessionStore, SqliteCheckpointer) and scripts/coding_tick.py defaults to
`sessions/coding_tick_errors.json`. Without redirection, any test that builds
one of these without an explicit path writes to production — which is how 89
`<MagicMock ...>` jobs ended up stuck in `running` in the real jobs table.

Strategy:
  * At conftest import (before any test module is collected or any
    `setUpClass` runs) every default path is pointed at a throwaway session
    directory, so nothing can reach production even outside a test function.
  * An autouse function-scoped fixture then repoints the defaults at the
    test's own `tmp_path` and resets the JobManager singleton before and after
    each test, so tests are isolated from each other too.
"""
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.runtime import job_manager as _job_manager_mod  # noqa: E402
from core.knowledge.memory import sqlite_session_store as _session_store_mod  # noqa: E402
from core.knowledge.memory import sqlite_checkpointer as _checkpointer_mod  # noqa: E402

PRODUCTION_SESSIONS_DIR = _job_manager_mod.SESSIONS_DIR

_ORIGINALS = {
    "jm_sessions": _job_manager_mod.SESSIONS_DIR,
    "jm_db": _job_manager_mod.DEFAULT_DB_PATH,
    "ss_sessions": _session_store_mod.SESSIONS_DIR,
    "ss_db": _session_store_mod.DEFAULT_DB_PATH,
    "ss_defaults": _session_store_mod.SqliteSessionStore.__init__.__defaults__,
    "cp_sessions": _checkpointer_mod.SESSIONS_DIR,
    "cp_db": _checkpointer_mod.DEFAULT_DB_PATH,
    "cp_defaults": _checkpointer_mod.SqliteCheckpointer.__init__.__defaults__,
    "tick_env": os.environ.get("AOC_TICK_ERROR_CACHE"),
}


def _redirect(setattr, setenv, sessions_dir: str) -> None:
    """Points every known production default path into `sessions_dir`."""
    os.makedirs(sessions_dir, exist_ok=True)
    db_path = os.path.join(sessions_dir, "memory.db")

    setattr(_job_manager_mod, "SESSIONS_DIR", sessions_dir)
    setattr(_job_manager_mod, "DEFAULT_DB_PATH", db_path)

    # These two bind the default into the signature at def time, so the
    # function defaults must be patched as well as the module constants.
    setattr(_session_store_mod, "SESSIONS_DIR", sessions_dir)
    setattr(_session_store_mod, "DEFAULT_DB_PATH", db_path)
    setattr(_session_store_mod.SqliteSessionStore.__init__, "__defaults__", (db_path,))

    setattr(_checkpointer_mod, "SESSIONS_DIR", sessions_dir)
    setattr(_checkpointer_mod, "DEFAULT_DB_PATH", db_path)
    setattr(_checkpointer_mod.SqliteCheckpointer.__init__, "__defaults__", (db_path,))

    setenv("AOC_TICK_ERROR_CACHE", os.path.join(sessions_dir, "coding_tick_errors.json"))


def _plain_setenv(name, value):
    os.environ[name] = value


# Session-wide fallback, applied at import time.
_SESSION_DIR = tempfile.mkdtemp(prefix="aoc-test-sessions-")
_redirect(setattr, _plain_setenv, _SESSION_DIR)
_job_manager_mod.JobManager._instance = None


def pytest_unconfigure(config):
    _job_manager_mod.JobManager._instance = None
    _job_manager_mod.SESSIONS_DIR = _ORIGINALS["jm_sessions"]
    _job_manager_mod.DEFAULT_DB_PATH = _ORIGINALS["jm_db"]
    _session_store_mod.SESSIONS_DIR = _ORIGINALS["ss_sessions"]
    _session_store_mod.DEFAULT_DB_PATH = _ORIGINALS["ss_db"]
    _session_store_mod.SqliteSessionStore.__init__.__defaults__ = _ORIGINALS["ss_defaults"]
    _checkpointer_mod.SESSIONS_DIR = _ORIGINALS["cp_sessions"]
    _checkpointer_mod.DEFAULT_DB_PATH = _ORIGINALS["cp_db"]
    _checkpointer_mod.SqliteCheckpointer.__init__.__defaults__ = _ORIGINALS["cp_defaults"]
    if _ORIGINALS["tick_env"] is None:
        os.environ.pop("AOC_TICK_ERROR_CACHE", None)
    else:
        os.environ["AOC_TICK_ERROR_CACHE"] = _ORIGINALS["tick_env"]
    shutil.rmtree(_SESSION_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolate_production_state(tmp_path, monkeypatch):
    """Per-test: fresh sessions dir under tmp_path and a fresh JobManager."""
    _redirect(monkeypatch.setattr, monkeypatch.setenv, str(tmp_path / "sessions"))
    _job_manager_mod.JobManager._instance = None
    yield
    _job_manager_mod.JobManager._instance = None
