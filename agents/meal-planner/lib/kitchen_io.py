"""Where the kitchen files live, and how they are read and rewritten.

INVENTORY.md, MEAL_LOGS.md and STAPLES.md sit in pkm/wallet/kitchen. A rewrite
holds the file's lock for its whole read-modify-write, so two tool calls in one
step never interleave, and goes through a temp file and `os.replace`, so a crash
mid-write leaves the old file whole.
"""
import os
import shutil
import tempfile
import threading
from contextlib import contextmanager
from typing import Optional

from core.tooling import pkm_path

INVENTORY = "INVENTORY.md"
MEAL_LOGS = "MEAL_LOGS.md"
STAPLES = "STAPLES.md"

_locks = {}
_locks_guard = threading.Lock()


def kitchen_path(name: str) -> str:
    """The path of one kitchen file, under the configured PKM directory."""
    return pkm_path("wallet", "kitchen", name)


def read_text(path: str) -> Optional[str]:
    """The file's text exactly as stored (line endings untouched), or None if it doesn't exist."""
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            return f.read()
    except FileNotFoundError:
        return None


@contextmanager
def locked(path: str):
    """Holds the lock for `path` (LangChain runs sync tools in worker threads)."""
    key = os.path.realpath(path)
    with _locks_guard:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        yield


def write_atomic(path: str, text: str) -> None:
    """Replaces the file with `text` in one step, keeping its permissions.

    Writes to the real path behind any symlink, so a linked file stays linked.
    """
    target = os.path.realpath(path)
    directory = os.path.dirname(target)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(target)}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(target):
            shutil.copymode(target, tmp)
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
