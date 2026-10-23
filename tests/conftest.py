"""Test-suite-wide isolation from production state.

Every store on memory.db (the session store and checkpointer, JobManager, the
inbox, the gate log, the schedule state) finds its file through
`Config().memory_db_path`: the real `sessions/memory.db` unless MEMORY_DB_PATH
is set. scripts/coding_tick.py defaults to `sessions/coding_tick_errors.json`.
Without redirection, any test that builds one of these without an explicit
path writes to production — which is how 89 `<MagicMock ...>` jobs ended up
stuck in `running` in the real jobs table.

Strategy:
  * At conftest import (before any test module is collected or any
    `setUpClass` runs) MEMORY_DB_PATH and the coding tick's variables point
    into a throwaway session directory, so nothing can reach production even
    outside a test function. Environment variables survive `Config().reset()`,
    which drops path overrides.
  * An autouse function-scoped fixture then repoints them at the test's own
    `tmp_path` and resets the JobManager singleton before and after each
    test, so tests are isolated from each other too.
  * Text-to-speech never reaches the network, in any test (`_no_tts_network`).
"""
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.runtime import job_manager as _job_manager_mod  # noqa: E402

_ORIGINALS = {
    "tick_env": os.environ.get("AOC_TICK_ERROR_CACHE"),
    "memory_db_env": os.environ.get("MEMORY_DB_PATH"),
}


def _redirect(setenv, sessions_dir: str) -> None:
    """Points every known production default path into `sessions_dir`."""
    os.makedirs(sessions_dir, exist_ok=True)

    # Config().memory_db_path, which every store on memory.db reads.
    setenv("MEMORY_DB_PATH", os.path.join(sessions_dir, "memory.db"))

    setenv("AOC_TICK_ERROR_CACHE", os.path.join(sessions_dir, "coding_tick_errors.json"))
    # The coding tick's host switch reads a pause file under the real sessions/;
    # a host that happens to be paused must not change what the tests see.
    setenv("CODING_TICK_PAUSE_FILE", os.path.join(sessions_dir, "coding_tick.paused"))
    setenv("CODING_TICK_ENABLED", "1")


def _plain_setenv(name, value):
    os.environ[name] = value


# Session-wide fallback, applied at import time.
_SESSION_DIR = tempfile.mkdtemp(prefix="aoc-test-sessions-")
_redirect(_plain_setenv, _SESSION_DIR)
_job_manager_mod.JobManager._instance = None


def pytest_unconfigure(config):
    _job_manager_mod.JobManager._instance = None
    if _ORIGINALS["tick_env"] is None:
        os.environ.pop("AOC_TICK_ERROR_CACHE", None)
    else:
        os.environ["AOC_TICK_ERROR_CACHE"] = _ORIGINALS["tick_env"]
    if _ORIGINALS["memory_db_env"] is None:
        os.environ.pop("MEMORY_DB_PATH", None)
    else:
        os.environ["MEMORY_DB_PATH"] = _ORIGINALS["memory_db_env"]
    shutil.rmtree(_SESSION_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolate_production_state(tmp_path, monkeypatch):
    """Per-test: fresh sessions dir under tmp_path and a fresh JobManager."""
    _redirect(monkeypatch.setenv, str(tmp_path / "sessions"))
    _job_manager_mod.JobManager._instance = None
    yield
    _job_manager_mod.JobManager._instance = None


@pytest.fixture(autouse=True)
def _ownership_prod_ready():
    """Per-test: a fresh Ownership singleton acting as prod with no claims, as
    if the control thread had already been read. Tests of ownership itself
    reset it again and configure their own role."""
    from core.channel.discord.ownership import Ownership

    Ownership.reset()
    Ownership().mark_ready()
    yield
    Ownership.reset()


@pytest.fixture(autouse=True)
def _no_inbox_listeners():
    """Per-test: nobody listens for new channel inbox rows. A voice manager
    that welcomed you subscribes for the process's life, so one test's
    must not hear the rows the next test writes."""
    from core.voice.briefing import writer

    writer._listeners.clear()
    yield
    writer._listeners.clear()


@pytest.fixture(autouse=True)
def _no_tts_network(monkeypatch):
    """Per-test: text-to-speech never reaches the network. Chirp counts as
    unavailable without running its gRPC check, and a real Chirp or edge-tts
    request fails; tests that speak use `FakeTTS` (tests/helpers.py).

    Suite-wide, not just under tests/core/voice/: building a Discord runner
    builds the voice stack, and its TTS engine starts Chirp's live check -- a
    gRPC probe and a Cloud TTS call with the .env key. The gRPC channel that
    leaves behind also writes a log line into the next child process's stderr.
    """
    from core.voice.engines import chirp_tts, edge_tts_stream, tts

    def refuse(*_args, **_kwargs):
        raise RuntimeError("tests must not reach a TTS service")

    async def refuse_async(*_args, **_kwargs):
        refuse()

    monkeypatch.setattr(tts, "_check", tts.ChirpCheck("disabled in tests", run=False))
    monkeypatch.setattr(chirp_tts, "client", refuse)
    monkeypatch.setattr(edge_tts_stream, "synthesize", refuse_async)
