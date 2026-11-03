"""Precondition evaluator for inbox state.

Evaluates the inbox directory to determine if actionable items exist for topic research.
"""

from pathlib import Path
import time


def evaluate(inbox_path: str = "pkm/inbox") -> tuple[bool, dict]:
    """Evaluate the specified inbox path for .md files and return (bool, status_dict)."""
    p = Path(inbox_path)
    if not p.is_dir():
        return False, {"status": "empty", "items": []}

    try:
        md_files = [f for f in p.iterdir() if f.is_file() and f.name.endswith(".md")]
    except OSError:
        return False, {"status": "empty", "items": []}

    if not md_files:
        return False, {"status": "empty", "items": []}

    filenames = sorted([f.name for f in md_files])

    # 1. multiple_items: > 1 .md file
    if len(md_files) > 1:
        return True, {"status": "multiple_items", "items": filenames}

    # Exactly 1 .md file
    single_file = md_files[0]
    try:
        st = single_file.stat()
        mtime = st.st_mtime
    except OSError:
        return False, {"status": "empty", "items": []}

    now = time.time()
    age_seconds = now - mtime

    # 2. new_item: <= 24 hours ago (86400 seconds)
    if age_seconds <= 86400:
        return True, {"status": "new_item", "items": filenames}

    # 3. stale_3_days: >= 72 hours ago (259200 seconds)
    if age_seconds >= 259200:
        return True, {"status": "stale_3_days", "items": filenames}

    # 4. pending_stale: between 24 and 72 hours ago
    return False, {"status": "pending_stale", "items": filenames}
