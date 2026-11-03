import os
import time
from pathlib import Path

SEVEN_DAYS_IN_SECONDS = 7 * 24 * 60 * 60  # 168 hours


def evaluate(topics_path: str = "pkm/wiki/topics") -> tuple[bool, dict]:
    path = Path(topics_path)
    if not path.exists() or not path.is_dir():
        return False, {"stalled_topics": []}

    stalled_topics = []
    now = time.time()

    try:
        entries = sorted(path.iterdir())
    except OSError:
        return False, {"stalled_topics": []}

    for entry in entries:
        if entry.is_file() and entry.name.endswith(".md"):
            if entry.name == "index.md":
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            age = now - mtime
            if age > SEVEN_DAYS_IN_SECONDS:
                stalled_topics.append(entry.name)

    is_stalled = len(stalled_topics) > 0
    return is_stalled, {"stalled_topics": stalled_topics}
