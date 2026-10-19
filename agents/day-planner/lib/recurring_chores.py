"""Recurring chores due, judged by when the user usually does each one.

Completing a recurring task (🔁) leaves a completed copy with its due (📅) or
scheduled (⏳) date and the day it was done (✅). A copy's *lead* is how many
days before its date it was done (negative: done late). A pending chore's
window opens at its date minus the longest lead among its recent copies:
before that the chore stays quiet; from then on it is flagged as due soon.
A chore with no completed copies opens `chore_lead_days` before its date
(RULES.md), and no window opens before a chore's start date (🛫).

A completed copy belongs to a pending chore when their titles match after
normalizing, or nearly match and recur on the same rule, because titles
drift: "Pay the water bill every 5th of the month (summer)" became
"... every 15th of the month".
"""
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from difflib import SequenceMatcher
from typing import List, NamedTuple, Optional, Sequence, Tuple

from agents.day_planner.lib.day_calendar import days, relative_phrase, short_date
from agents.day_planner.lib.task_cache import RECURRENCE, cache_path, display_title, read_day, recurrence_rule
from core.tooling import Report

# How many of a chore's most recent completions shape its window.
HISTORY_LIMIT = 8
# Title similarity at which a completion on the same rule counts as the same chore.
MATCH_RATIO = 0.85

_PENDING_SQL = f"""
SELECT raw_title, priority, due_date, scheduled_date FROM tasks
WHERE status = 'todo' AND raw_title LIKE '%{RECURRENCE}%'
"""
_DONE_SQL = f"""
SELECT raw_title, due_date, scheduled_date, completed_date FROM tasks
WHERE status = 'completed' AND raw_title LIKE '%{RECURRENCE}%'
  AND completed_date IS NOT NULL AND COALESCE(due_date, scheduled_date) IS NOT NULL
"""
_START = re.compile(r"🛫\s*(\d{4}-\d{2}-\d{2})")


class Completion(NamedTuple):
    key: str  # normalized title
    rule: str
    on: date  # the copy's due or scheduled date
    done: date


@dataclass(frozen=True)
class Chore:
    """One pending chore, judged against a day."""
    title: str
    priority: str
    kind: str  # "due" or "scheduled": which date the chore keeps
    on: date
    days_left: int  # negative when overdue
    opens: date  # first day it is flagged
    leads: Tuple[int, ...]  # days early of the completions behind the window (negative: late)
    state: str  # "overdue", "today", "soon" or "quiet"
    start: Optional[date] = None  # its start date (🛫), if any


@dataclass
class ChoresDue:
    today: date
    lead_days: int  # chore_lead_days: the window of a chore with no completion history
    overdue: List[Chore] = field(default_factory=list)
    due_today: List[Chore] = field(default_factory=list)
    soon: List[Chore] = field(default_factory=list)
    quiet: List[Chore] = field(default_factory=list)
    undated: List[str] = field(default_factory=list)  # titles of pending chores with neither a due nor a scheduled date


def chores_due(conn: sqlite3.Connection, today: date, lead_days: int, report: Report) -> ChoresDue:
    """Every pending recurring chore in tasks.db, sorted into overdue / today / soon / quiet.

    A row whose date isn't a real date is left out and returned raw in `report`.
    """
    source = cache_path(conn)
    unreadable = []
    history = []
    done_rows = conn.execute(_DONE_SQL).fetchall()
    for r in done_rows:
        on, done = _anchor(r)[1], read_day(r["completed_date"])
        if on is None or done is None:
            unreadable.append(r["raw_title"])
            continue
        history.append(Completion(_key(r["raw_title"]), recurrence_rule(r["raw_title"]), on, done))
    chores = ChoresDue(today=today, lead_days=lead_days)
    buckets = {"overdue": chores.overdue, "today": chores.due_today, "soon": chores.soon, "quiet": chores.quiet}
    pending_rows = conn.execute(_PENDING_SQL).fetchall()
    for row in pending_rows:
        raw = row["raw_title"]
        if not (row["due_date"] or row["scheduled_date"]):
            chores.undated.append(display_title(raw))
            continue
        kind, on = _anchor(row)
        start_text = _START.search(raw or "")
        start = read_day(start_text.group(1)) if start_text else None
        if on is None or (start_text and start is None):
            unreadable.append(raw)
            continue
        matched = sorted(_matching(_key(raw), recurrence_rule(raw), history), key=lambda c: c.done, reverse=True)
        leads = [(c.on - c.done).days for c in matched[:HISTORY_LIMIT]]
        chore = assess(display_title(raw), row["priority"] or "", kind, on, leads, today, lead_days, start=start)
        buckets[chore.state].append(chore)
        report.trace(window_trace(chore, lead_days, today))
    chores.overdue.sort(key=lambda c: (c.on, c.title))
    chores.due_today.sort(key=lambda c: c.title)
    chores.soon.sort(key=lambda c: (c.on, c.title))
    chores.quiet.sort(key=lambda c: (c.opens, c.title))
    report.read(source, records=len(pending_rows) + len(done_rows) - len(unreadable), skipped=len(unreadable))
    report.unparsed(source, unreadable)
    return chores


def assess(
    title: str,
    priority: str,
    kind: str,
    on: date,
    leads: Sequence[int],
    today: date,
    lead_days: int,
    start: Optional[date] = None,
) -> Chore:
    """Judges one chore whose past completions had `leads` (days early; negative: late).

    The window opens `max(leads)` days before the chore's date, or
    `lead_days` (chore_lead_days) before it with no history, and never
    before its start date (🛫).
    """
    opens = on - timedelta(days=max(leads) if leads else lead_days)
    if start is not None and start > opens:
        opens = start
    days_left = (on - today).days
    if days_left < 0:
        state = "overdue"
    elif days_left == 0:
        state = "today"
    elif today >= opens:
        state = "soon"
    else:
        state = "quiet"
    return Chore(title, priority, kind, on, days_left, opens, tuple(leads), state, start)


def window_trace(chore: Chore, lead_days: int, today: date) -> str:
    """How `chore`'s window and state were reached, for explain mode."""
    if chore.leads:
        leads = ", ".join(str(n) for n in chore.leads)
        count = f"{len(chore.leads)} completion{'' if len(chore.leads) == 1 else 's'}"
        why = f"days early at its last {count} (negative: late): {leads}; the most is {max(chore.leads)}"
    else:
        why = f"no completion history, so chore_lead_days ({lead_days})"
    line = f"{chore.title}: {chore.kind} {chore.on.isoformat()}; {why}"
    if chore.start is not None and chore.opens == chore.start:
        line += f"; not before its start date {chore.start.isoformat()}"
    return f"{line} → opens {chore.opens.isoformat()}; {chore.state} on {today.isoformat()}"


def format_chores(chores: ChoresDue, within_days: int = 0) -> str:
    """The chores as lines ready to relay; every number is already computed.

    `within_days` > 0 also lists the quiet chores whose date falls within
    that many days, so "which chores are due this week?" is one answer.
    """
    today = chores.today
    lines = []
    for label, group in (("Overdue", chores.overdue), ("Due today", chores.due_today), ("Due soon", chores.soon)):
        if not group:
            lines.append(f"{label}: none")
            continue
        lines.append(f"{label}:")
        lines.extend(f"- {_chore_line(c, today, chores.lead_days)}" for c in group)
    quiet = chores.quiet
    if within_days > 0:
        upcoming = [c for c in quiet if c.days_left <= within_days]
        if upcoming:
            lines.append(f"Also due within {days(within_days)}, before their usual window:")
            lines.extend(f"- {_chore_line(c, today, chores.lead_days)}" for c in upcoming)
        quiet = [c for c in quiet if c.days_left > within_days]
    if quiet:
        first = min(quiet, key=lambda c: (c.opens, c.title))
        lines.append(
            f"Quiet until their usual window: {len(quiet)} "
            f"(next: {first.title}, from {short_date(first.opens, today)})"
        )
    if chores.undated:
        lines.append(f"Not judged (no due or scheduled date): {' · '.join(chores.undated)}")
    return "\n".join(lines)


def _chore_line(chore: Chore, today: date, lead_days: int) -> str:
    title = f"{chore.title} {chore.priority}".rstrip()
    line = f"{title}: {relative_phrase(chore.kind, chore.on, today)}"
    if chore.leads:
        return f"{line}; {_usual(chore.leads)}"
    if chore.state == "soon":
        return f"{line}; no completion history, so flagged {days(lead_days)} ahead"
    return line


def _usual(leads: Sequence[int]) -> str:
    low, high = min(leads), max(leads)
    count = f"{len(leads)} past completion{'' if len(leads) == 1 else 's'}"
    if low == high:
        span = _lead(low)
    elif low >= 0:
        span = f"{low}–{high} days early" if low else f"on the day to {days(high)} early"
    elif high <= 0:
        span = f"{-high}–{-low} days late" if high else f"{days(-low)} late to on the day"
    else:
        span = f"{days(-low)} late to {days(high)} early"
    return f"usually done {span} ({count})"


def _lead(n: int) -> str:
    if n == 0:
        return "on the day"
    return f"{days(n)} early" if n > 0 else f"{days(-n)} late"


def _anchor(row) -> Tuple[str, Optional[date]]:
    """Which date the task keeps, and that date: its due date, else its scheduled date."""
    if row["due_date"]:
        return "due", read_day(row["due_date"])
    if row["scheduled_date"]:
        return "scheduled", read_day(row["scheduled_date"])
    return "due", None


def _key(raw_title: str) -> str:
    """The title, lowercased, with only letters and digits: what matching compares."""
    title = display_title(raw_title, limit=500).lower()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in title).split())


def same_chore(pending: str, done: str) -> bool:
    """Whether a completed copy (its raw task line) belongs to a pending chore: their titles match
    after normalizing, or they recur on the same rule and their titles are MATCH_RATIO alike.
    """
    return _alike(_key(pending), recurrence_rule(pending), _key(done), recurrence_rule(done))


def _alike(key: str, rule: str, other_key: str, other_rule: str) -> bool:
    return key == other_key or (rule == other_rule and SequenceMatcher(None, key, other_key).ratio() >= MATCH_RATIO)


def _matching(key: str, rule: str, history: List[Completion]):
    for item in history:
        if _alike(key, rule, item.key, item.rule):
            yield item
