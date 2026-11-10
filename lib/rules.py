"""Rules parser utility for extracting configuration thresholds from Markdown files."""

import re
from pathlib import Path


def get_threshold(key: str, file_path: str = "RULES.md") -> str | None:
    """
    Parses the Markdown file and extracts the value for the given key.
    Expected Markdown format for a threshold: `**<key>**: <value>` or `<key>: <value>` on a single line.
    """
    if not key:
        return None
    try:
        path = Path(file_path)
        if not path.is_file():
            return None
        content = path.read_text(encoding="utf-8")
    except Exception:
        return None

    pattern = rf"(?:\*\*{re.escape(key)}\*\*|\b{re.escape(key)}\b)\s*:\s*(.+)"
    match = re.search(pattern, content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None
