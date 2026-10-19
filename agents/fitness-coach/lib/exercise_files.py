"""Where the home-workout data lives, and safe reads and writes of it.

pkm/wallet/exercise/ holds muscle_recovery.json, equipment.md,
library/<group>.json, progression.jsonl and workout_history.jsonl. Rewrites are
atomic (temp file + os.replace) and keep the file's mode; appends always leave
the file ending in a newline.
"""
import json
import os
import stat
import tempfile
from typing import Any, Iterable, List, Optional

from core.tooling import pkm_path

RECOVERY_FILE = "muscle_recovery.json"
EQUIPMENT_FILE = "equipment.md"
LIBRARY_DIR = "library"
PROGRESSION_FILE = "progression.jsonl"
HISTORY_FILE = "workout_history.jsonl"


def data_path(name: str) -> str:
    """The absolute path of `name` in pkm/wallet/exercise/."""
    return pkm_path("wallet", "exercise", name)


def read_text(path: str) -> Optional[str]:
    """The file's text, or None when it doesn't exist."""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def read_json(path: str) -> Any:
    """Parsed JSON, or None when the file doesn't exist. Raises ValueError on bad JSON."""
    text = read_text(path)
    return None if text is None or not text.strip() else json.loads(text)


def read_lines(path: str) -> List[str]:
    """The file's non-blank lines without their newlines; [] when it doesn't exist."""
    text = read_text(path) or ""
    return [line for line in text.splitlines() if line.strip()]


def parse_json_line(line: str) -> Optional[dict]:
    """One JSONL line as a dict, or None when it isn't a JSON object."""
    try:
        value = json.loads(line)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def dump_line(obj: dict) -> str:
    """A compact JSONL line, the format the skill's examples use."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def write_atomic(path: str, text: str) -> None:
    """Replaces the file in one step, so a crash never leaves it half written.

    Writes through a symlinked file to its target, and keeps the old mode
    (mkstemp would otherwise leave a private 0600 file behind).
    """
    target = os.path.realpath(path)
    folder = os.path.dirname(target)
    os.makedirs(folder, exist_ok=True)
    try:
        mode = stat.S_IMODE(os.stat(target).st_mode)
    except FileNotFoundError:
        mode = 0o644
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-", suffix=os.path.basename(target))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def append_lines(path: str, lines: Iterable[str]) -> None:
    """Appends each line plus a newline, after a newline if the file lacks a final one."""
    lines = list(lines)
    if not lines:
        return
    target = os.path.realpath(path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    existing = read_text(target) or ""
    lead = "\n" if existing and not existing.endswith("\n") else ""
    with open(target, "a", encoding="utf-8") as f:
        f.write(lead + "".join(f"{line}\n" for line in lines))
