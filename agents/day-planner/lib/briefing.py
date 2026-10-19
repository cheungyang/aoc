"""The morning briefing: what the day planner's routine reads before it plans, as one text.

Dates, chores and tasks are computed here. The notes (yesterday's, today's,
the weekly focus) come back as written: the agent reads them. A daily note
nothing is written in yet, still the template it was created from, comes
back as one line saying so. Each section is read on its own: a broken cache
or note costs that section (it says "unavailable", and the report says why),
never the whole briefing.
"""
import functools
import os
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Optional

from agents.day_planner.lib import journal_notes, project_slate
from agents.day_planner.lib.day_calendar import (
    iso_week_label,
    locale_week,
    long_date,
    past_days,
    short_date,
    week_start,
)
from agents.day_planner.lib.recurring_chores import chores_due, format_chores
from agents.day_planner.lib.task_cache import open_cache, projects_db_path, tasks_db_path
from core.tooling import Report, display_path, pkm_path

_FOCUS_STATES = {
    "template": "still the template's A, B, C",
    "empty": "the section is empty",
    "no heading": "no \"Next week's focus\" heading",
    "no note": "no note",
}
# What the briefing says of a daily note still exactly the template (journal_notes.is_untouched).
_UNTOUCHED_NOTE = "created from the template, nothing written yet"


class _Unavailable(Exception):
    """A section's source is missing; the report already says so."""


def build_briefing(now: datetime, report: Report, rules: Dict[str, Any], include_new_note: bool = False) -> str:
    """The briefing for `now`'s date (pass a local time). What couldn't be read goes in `report`.

    `rules` are day-planner's thresholds (lib/rules.py over RULES.md).
    `include_new_note` adds the text to create today's note with when it does
    not exist yet; only a turn that may log the plan needs it.
    """
    pkm_dir, today = pkm_path(), now.date()
    tasks_path, projects_path = tasks_db_path(), projects_db_path()
    tasks, projects = open_cache(tasks_path, report), open_cache(projects_path, report)
    template = _template_reader(pkm_dir, report)

    def need(conn: Optional[sqlite3.Connection], path: str) -> sqlite3.Connection:
        if conn is None:
            raise _Unavailable(f"{display_path(path)} not found")
        return conn

    try:
        sections = [
            header(today, report),
            _section("Daily notes in the last 7 days", report, lambda: _last_days(pkm_dir, today)),
            _section("This week's focus", report, lambda: _focus(pkm_dir, today, report)),
            _section("Yesterday", report, lambda: _yesterday(pkm_dir, today, report, template)),
            _section("Today's note", report, lambda: _today(pkm_dir, today, report, template, include_new_note)),
            _section("Recurring chores", report,
                     lambda: format_chores(chores_due(need(tasks, tasks_path), today, rules["chore_lead_days"], report))),
            _section(
                f"Open tasks due by {project_slate.due_horizon(today)} or scheduled on or before today (chores excluded)",
                report, lambda: _due(need(tasks, tasks_path), today, report),
            ),
            _section(
                f"Active {today.year} projects (executing or planning) by priority, with their top open tasks",
                report, lambda: project_slate.format_projects(project_slate.active_projects(
                    need(projects, projects_path), need(tasks, tasks_path), today, report)),
            ),
        ]
    finally:
        for conn in (tasks, projects):
            if conn is not None:
                conn.close()
    return "\n\n".join(sections)


def header(today: date, report: Report) -> str:
    """Today's date, its ISO week and its vault week, with the vault week's first and last day."""
    start = week_start(today)
    week_year, week = locale_week(today)
    report.trace(f"vault week {week_year}-W{week}: weeks start on Sunday, and week 1 is the one holding "
                 f"January 1st (from {week_start(date(week_year, 1, 1)).isoformat()}); this one is from "
                 f"{start.isoformat()}")
    return (
        f"{long_date(today)} · ISO week {iso_week_label(today)} · vault week {week_year}-W{week} "
        f"({short_date(start, today)} to {short_date(start + timedelta(days=6), today)})"
    )


def _section(title: str, report: Report, body: Callable[[], str]) -> str:
    try:
        text = body()
    except _Unavailable as e:
        return f"{title}: unavailable ({e})"
    except Exception as e:  # one unreadable source must not cost the rest
        report.problem(title, f"couldn't be read ({type(e).__name__}: {e})")
        return f"{title}: unavailable ({type(e).__name__}: {e})"
    return f"{title}:\n{text}" if "\n" in text or text.startswith("- ") else f"{title}: {text}"


def _last_days(pkm_dir: str, today: date) -> str:
    days = past_days(today)
    have = [d for d in days if os.path.isfile(os.path.join(pkm_dir, journal_notes.daily_note(d)))]
    missing = [d for d in days if d not in have]
    if not have:
        return f"none ({short_date(days[0], today)} to {short_date(days[-1], today)})"
    listed = ", ".join(short_date(d, today) for d in have)
    if not missing:
        return listed
    return f"{listed}; none on {', '.join(short_date(d, today) for d in missing)}"


def _focus(pkm_dir: str, today: date, report: Report) -> str:
    focus = journal_notes.read_weekly_focus(pkm_dir, today, report)
    if not focus.items:
        looked = "; ".join(f"{source.name}: {_FOCUS_STATES[source.state]}" for source in focus.looked)
        return f"not set ({looked})"
    shown, cut = journal_notes.clip("\n".join(focus.items))
    more = f"\n[{cut} more lines in the section]" if cut else ""
    return f"(from {focus.source}, \"Next week's focus\", as written)\n{shown}{more}"


def _yesterday(pkm_dir: str, today: date, report: Report, template: Callable[[], Optional[str]]) -> str:
    day = today - timedelta(days=1)
    rel = journal_notes.daily_note(day)
    head = f"{long_date(day)}, {journal_notes.note_path(rel)}"
    shown = _note(head, os.path.join(pkm_dir, rel), report, template)
    return f"{head}: no note" if shown is None else shown


def _today(pkm_dir: str, today: date, report: Report, template: Callable[[], Optional[str]],
           include_new_note: bool) -> str:
    rel = journal_notes.daily_note(today)
    path = journal_notes.note_path(rel)
    shown = _note(path, os.path.join(pkm_dir, rel), report, template)
    if shown is not None:
        return shown
    line = f"{path}: not created yet"
    if not include_new_note:
        return line
    text = template()
    if text is None:
        template_path = os.path.join(pkm_dir, journal_notes.FLEETING_TEMPLATE)
        report.problem(template_path, "not found; today's note can't be created from it")
        return f"{line}; its template {journal_notes.note_path(journal_notes.FLEETING_TEMPLATE)} is missing"
    new_note = journal_notes.with_journal_keys(text, "fleeting", today)
    return f"{line}. New-note text (the template as the Journals plugin writes it):\n{_fenced(new_note.rstrip())}"


def _note(head: str, path: str, report: Report, template: Callable[[], Optional[str]]) -> Optional[str]:
    """`head` and the daily note at `path`: as written, fenced, cut past NOTE_CHAR_LIMIT with the count
    of lines left out; or one line while nothing is written in it yet. None when it is missing.
    """
    text = journal_notes.read_note(path)
    if text is None:
        return None
    if _untouched(text, template):
        report.read(path)
        report.trace(f"{display_path(path)}: the same as {journal_notes.note_path(journal_notes.FLEETING_TEMPLATE)} "
                     "once line endings, trailing whitespace and the Journals plugin's keys are set aside")
        return f"{head}: {_UNTOUCHED_NOTE}"
    shown, cut = journal_notes.clip(text)
    report.read(path, skipped=cut)
    more = f"\n[{cut} more lines in the note]" if cut else ""
    return f"{head}, as written:\n{_fenced(shown)}{more}"


def _untouched(text: str, template: Callable[[], Optional[str]]) -> bool:
    try:
        blank = template()
    except OSError:  # an unreadable template costs only the shortening: the note comes back in full
        return False
    return blank is not None and journal_notes.is_untouched(text, blank)


def _template_reader(pkm_dir: str, report: Report) -> Callable[[], Optional[str]]:
    """The daily template's text, read when a section first asks for it, then kept; None when it is missing."""
    path = os.path.join(pkm_dir, journal_notes.FLEETING_TEMPLATE)

    @functools.cache
    def template() -> Optional[str]:
        text = journal_notes.read_note(path)
        if text is not None:
            report.read(path)
        return text

    return template


def _fenced(text: str) -> str:
    fence = "~~~"
    while fence in text:
        fence += "~"
    return f"{fence}markdown\n{text}\n{fence}"


def _due(tasks: sqlite3.Connection, today: date, report: Report) -> str:
    lines = project_slate.due_tasks(tasks, today, report)
    return "\n".join(f"- {line}" for line in lines) if lines else "none"
