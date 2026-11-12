"""Gemma's daily work journals, in pkm/vault/journals/work/.

One Markdown file per workday, named for its date (`2026-11-12.md`), holds that
day's update from the crop agent. The vault's journal template may open the
file with only its front matter (`journal`, `journal-date`); until
something is written under it, the day has no entry. Other files in the folder,
such as weekly_rollups.md, aren't daily journals.

The scans read these to decide whether a run has work: missing_journal (today's
entry isn't in yet) and journal_updates (a recent one is new).
"""
import os
import re
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from core.tooling import Report, pkm_path

FOLDER = ("vault", "journals", "work")
_DAILY = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")


@dataclass(frozen=True)
class Journal:
    """One daily journal: its date, its path, and when it was last modified (epoch seconds)."""
    day: date
    path: str
    modified: float


def journals_folder() -> str:
    """pkm/vault/journals/work, under the configured PKM."""
    return pkm_path(*FOLDER)


def journal_path(day: date) -> str:
    """Where `day`'s journal goes: `<journals folder>/<YYYY-MM-DD>.md`."""
    return os.path.join(journals_folder(), f"{day.isoformat()}.md")


def read_journal(path: str) -> Optional[str]:
    """The journal's text, or None when there is no such file."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except FileNotFoundError:
        return None


def has_entry(text: str) -> bool:
    """Whether a journal holds anything besides its front matter."""
    lines = text.lstrip("\ufeff").splitlines()
    if lines and lines[0].strip() == "---":
        closing = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
        if closing is not None:
            lines = lines[closing + 1:]
    return any(line.strip() for line in lines)


def daily_journals(folder: str) -> List[Journal]:
    """Every daily journal in `folder`, newest date first."""
    found = []
    for name in os.listdir(folder):
        match = _DAILY.match(name)
        path = os.path.join(folder, name)
        if not match or not os.path.isfile(path):
            continue
        try:
            day = date.fromisoformat(match.group(1))
            modified = os.path.getmtime(path)
        except (ValueError, OSError):  # not a real date (2026-13-01.md), or gone since the listing
            continue
        found.append(Journal(day, path, modified))
    return sorted(found, key=lambda journal: journal.day, reverse=True)


def recent_journals(folder: str, count: int, report: Report) -> List[Journal]:
    """The `count` newest daily journals in `folder` that have an entry, newest first.

    A journal with only its front matter is passed over, so it never pushes an
    entry out of the count. So is one that can't be read, as an issue in `report`.
    """
    recent: List[Journal] = []
    for journal in daily_journals(folder):
        if len(recent) >= count:
            break
        try:
            text = read_journal(journal.path)
        except OSError as e:
            report.problem(journal.path, f"can't be read ({type(e).__name__}: {e})")
            continue
        if text is not None and has_entry(text):
            recent.append(journal)
    return recent
