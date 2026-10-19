"""The weekly review's note: the week it covers, and how the review is saved in it.

The review runs on Friday afternoon (agent.json) and covers the vault week
holding the latest Friday, today included: on Friday or Saturday the current
week, from Sunday to Thursday the week before. Its wins and next week's focus
go into that week's note (`journal_notes.weekly_note`), whose "Next week's
focus" the daily notes embed all the following week.

A note that doesn't exist yet is the weekly template exactly as the Journals
plugin writes it: Templater syntax as written, and the plugin's keys in the
frontmatter, so the plugin takes the note as its own instead of adding its
template under it. A note that exists gets the keys if it lacks them. Then the
sections are filled (`note_sections`); every other line stays as written, and
the note is rewritten in one step (temp file + `os.replace`).
"""
import os
import stat
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import List, Optional, Sequence, Tuple

from agents.day_planner.lib import journal_notes
from agents.day_planner.lib.day_calendar import short_date, week_start
from agents.day_planner.lib.note_sections import Filled, fill_sections
from core.tooling import DataProblem, Report

REVIEW_WEEKDAY = 4  # Friday (Monday is 0), the weekly-review schedule's day

_writing = threading.Lock()  # one rewrite at a time: LangChain runs sync tools in worker threads


@dataclass
class Saved:
    start: date  # the Sunday that starts the week
    created: bool  # a new note, from the template
    keys_added: bool  # an existing note that lacked the plugin's keys
    filled: List[Filled] = field(default_factory=list)


def review_week(today: date) -> date:
    """The Sunday that starts the vault week under review: the week holding the latest Friday, today included."""
    return week_start(today - timedelta(days=(today.weekday() - REVIEW_WEEKDAY) % 7))


def save_review(pkm_dir: str, start: date, sections: Sequence[Tuple[str, str]], replace: bool,
                report: Report) -> Saved:
    """Writes `sections` into the note of the week starting `start`, created from the template if missing.

    A missing template, or a note that isn't UTF-8 text, fails through `report`;
    a section the note can't take raises `note_sections.SectionError`. Either way
    nothing is written.
    """
    path = os.path.join(pkm_dir, journal_notes.weekly_note(start))
    with _writing:
        text = _read(path, report)
        created = text is None
        if created:
            template = os.path.join(pkm_dir, journal_notes.WEEKLY_TEMPLATE)
            text = _read(template, report)
            if text is None:
                report.missing(template, "not found; the week's note can't be created from it")
                report.raise_if_failed()
        keyed = journal_notes.with_journal_keys(text, "weekly", start)
        new_text, filled = fill_sections(keyed, sections, replace)
        _write_atomic(path, new_text)
    return Saved(start, created, not created and keyed != text, filled)


def describe(saved: Saved, today: date) -> str:
    """Where the review went and what each section got, ready to relay."""
    note = journal_notes.note_path(journal_notes.weekly_note(saved.start))
    week = f"{short_date(saved.start, today)} to {short_date(saved.start + timedelta(days=6), today)}"
    how = ("a new note from the weekly template" if saved.created
           else "the existing note, with the Journals plugin's keys added" if saved.keys_added
           else "the existing note")
    lines = [f"Saved in {note} (the week of {week}), {how}:"]
    for section in saved.filled:
        if not section.lines:
            lines.append(f"- {section.heading}: nothing to write")
            continue
        count = f"{section.lines} line{'' if section.lines == 1 else 's'}"
        lines.append(f"- {section.heading}: {count}" + (", in place of:" if section.replaced else ""))
        lines.extend(f"    {line}" for line in section.replaced)
    return "\n".join(lines)


def _read(path: str, report: Report) -> Optional[str]:
    """The file's text exactly as stored, or None when it doesn't exist. Text that isn't UTF-8 fails."""
    try:
        with open(path, encoding="utf-8", newline="") as f:
            text = f.read()
    except FileNotFoundError:
        return None
    except UnicodeDecodeError:
        report.missing(path, "isn't UTF-8 text, so it's left as it is")
        raise DataProblem(report)
    report.read(path)
    return text


def _write_atomic(path: str, text: str) -> None:
    """Replaces the file in one step, keeping its mode; through a symlink, to its target."""
    target = os.path.realpath(path)
    folder = os.path.dirname(target)
    os.makedirs(folder, exist_ok=True)
    try:
        mode = stat.S_IMODE(os.stat(target).st_mode)
    except FileNotFoundError:
        mode = 0o644
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-", suffix=os.path.basename(target))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
