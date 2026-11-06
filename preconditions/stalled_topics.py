"""Precondition evaluator for stalled topics.

Evaluates pkm/wiki/topics/ to identify active learning topics that have stalled,
triggering the topic-researcher agent.
"""

from pathlib import Path
import re
import time

SEVEN_DAYS_IN_SECONDS = 7 * 24 * 3600  # 168 hours


def _is_active_topic(file_path: Path) -> bool:
    """Check if file has YAML frontmatter with status: active."""
    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    match = re.match(r"^\s*---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|$)", content, re.DOTALL)
    if not match:
        return False

    frontmatter = match.group(1)
    for line in frontmatter.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            if key.strip() == "status":
                val = val.strip().strip("'\"")
                return val == "active"

    return False


def evaluate(topics_path: str = "pkm/wiki/topics") -> tuple[bool, dict]:
    """Evaluate the specified topics path for stalled active topic .md files."""
    p = Path(topics_path)
    if not p.is_dir():
        return False, {"stalled_topics": []}

    try:
        md_files = [
            f for f in p.iterdir()
            if f.is_file() and f.name.endswith(".md") and f.name != "index.md"
        ]
    except OSError:
        return False, {"stalled_topics": []}

    now = time.time()
    stalled_topics = []

    for file_path in md_files:
        if not _is_active_topic(file_path):
            continue

        try:
            mtime = file_path.stat().st_mtime
        except OSError:
            continue

        if (now - mtime) > SEVEN_DAYS_IN_SECONDS:
            stalled_topics.append(file_path.name)

    stalled_topics.sort()

    if stalled_topics:
        return True, {"stalled_topics": stalled_topics}

    return False, {"stalled_topics": []}
