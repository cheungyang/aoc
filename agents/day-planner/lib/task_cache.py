"""Read-only access to the task and project caches, and task titles fit to read out.

The caches (`tasks.db`, `projects.db`) are rebuilt from the vault by the nightly
sync. Planning only reads them, so they are opened read-only: a missing file
raises instead of being created empty, and nothing here can write.
"""
import re
import sqlite3
from contextlib import ExitStack, contextmanager
from datetime import date
from typing import Iterator, Optional

from core.storage import Store, StoreMissing
from core.tooling import Report

PRIORITY_ICONS = ("🔺", "⏫", "🔼", "🔽", "⏬")
RECURRENCE = "🔁"

_COMMENT = re.compile(r"%%.*?%%", re.DOTALL)
_TICKTICK_LINK = re.compile(r"\[link\]\([^)]*\)", re.IGNORECASE)
_WIKI_LINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_DATED = re.compile(r"[✅❌📅⏳➕🛫]\s*\d{4}-\d{2}-\d{2}")
_RULE = re.compile(r"🔁\s*every\b[^📅⏳🛫✅❌➕🔺⏫🔼🔽⏬#%\[\]]*", re.IGNORECASE)
_MARKERS = re.compile(r"[🔁🔒🛫]|🕸️?")
_TAG = re.compile(r"(?:^|\s)#[\w/\-]+")
_URL = re.compile(r"https?://\S+")


@contextmanager
def open_cache(store: Store, report: Report, required: bool = False) -> Iterator[Optional[sqlite3.Connection]]:
    """`store.read_only()` for the block, or None when its file is missing.

    A missing file goes in `report`: as required data (`failed`) or not (`partial`).
    """
    with ExitStack() as stack:
        try:
            conn = stack.enter_context(store.read_only())
        except StoreMissing:
            (report.missing if required else report.problem)(store.path, "not found")
            conn = None
        yield conn


def cache_path(conn: sqlite3.Connection) -> str:
    """The file `conn` reads, for naming it in a report ("" for an in-memory database)."""
    row = conn.execute("PRAGMA database_list").fetchone()
    return (row[2] if row else "") or ""


def read_day(value: Optional[str]) -> Optional[date]:
    """A cached YYYY-MM-DD date, or None when it is empty or not a real date (2026-02-30)."""
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def recurrence_rule(raw_title: str) -> str:
    """The task's 🔁 rule, lowercased ('every month'), or ''."""
    match = _RULE.search(raw_title or "")
    return " ".join(match.group(0)[1:].split()).lower() if match else ""


def display_title(raw_title: str, limit: int = 90) -> str:
    """A task line's text without its metadata, links reduced to their text.

    Rebuilt from the raw line because the cached `title` keeps the tail of a
    link whose URL holds parentheses ("Book Haircut?loc=...").
    """
    text = _COMMENT.sub(" ", raw_title or "")
    text = _TICKTICK_LINK.sub(" ", text)
    text = _WIKI_LINK.sub(lambda m: m.group(2) or m.group(1), text)
    text = _strip_links(text)
    text = _URL.sub(" ", text)
    text = _DATED.sub(" ", text)
    text = _RULE.sub(" ", text)
    text = _MARKERS.sub(" ", text)
    for icon in PRIORITY_ICONS:
        text = text.replace(icon, " ")
    text = _TAG.sub(" ", text)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _strip_links(text: str) -> str:
    """'[label](url)' -> 'label'; brackets may nest in the label, parentheses in the URL.

    A link whose URL never closes keeps its label, and the URL after it stays
    for the caller to drop.
    """
    out, i = [], 0
    while True:
        close = text.find("](", i)
        if close == -1:
            break
        open_, depth = close - 1, 1
        while open_ >= i and depth:
            depth += {"]": 1, "[": -1}.get(text[open_], 0)
            open_ -= 1
        if depth:  # no opening bracket: not a link
            out.append(text[i:close + 2])
            i = close + 2
            continue
        end, parens = close + 2, 1
        while end < len(text) and parens:
            parens += {"(": 1, ")": -1}.get(text[end], 0)
            end += 1
        out.append(text[i:open_ + 1])
        out.append(text[open_ + 2:close])
        out.append(" ")
        i = end if not parens else close + 2
    out.append(text[i:])
    return "".join(out)
