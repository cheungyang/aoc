"""The journal notes the briefing reads, returned as written; and new notes, as the Journals plugin writes them.

Daily notes live at `vault/journals/fleeting/YYYY-MM-DD.md` (template
`templates/Fleeting Pages.md`); weekly notes at `vault/journals/weekly/` under
the vault week's name (see `day_calendar`; template `templates/Weekly Review.md`).
The Journals plugin adds `journal`/`journal-date` frontmatter to the notes it
creates, and recognizes its notes by them, so a note created here carries the same keys.

The agent reads the notes. Code only finds them, finds a weekly note's
"Next week's focus" section, cuts a long note at a line, saying how many
lines it left out, and tells whether a daily note is still the template it
was created from. Nothing written in a note is interpreted or dropped.
"""
import os
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import List, Optional, Tuple

from agents.day_planner.lib.day_calendar import vault_week_names
from core.tooling import Report

FLEETING_DIR = os.path.join("vault", "journals", "fleeting")
WEEKLY_DIR = os.path.join("vault", "journals", "weekly")
FLEETING_TEMPLATE = os.path.join("templates", "Fleeting Pages.md")
WEEKLY_TEMPLATE = os.path.join("templates", "Weekly Review.md")
# How the agent's tools name pkm files (relative to the project root).
PKM_PREFIX = "pkm"

# A longer note is cut at a line, with the count of the lines left out.
NOTE_CHAR_LIMIT = 3000

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_FOCUS = re.compile(r"next week.?s focus", re.IGNORECASE)
_PLACEHOLDER_ITEMS = ["A", "B", "C"]
# The frontmatter keys `with_journal_keys` adds; `journal-date` differs from note to note.
_JOURNAL_KEYS = ("journal", "journal-date")


@dataclass
class FocusSource:
    """One weekly note looked at for this week's focus."""
    name: str  # the note's name, e.g. "2026-W41"
    path: str
    state: str  # "set", "template" (still A, B, C), "empty", "no heading" or "no note"
    lines: List[str] = field(default_factory=list)  # the section as written


@dataclass
class WeeklyFocus:
    items: List[str]  # the focus in force, as written; [] when not set
    source: str = ""  # the weekly note it came from
    looked: List[FocusSource] = field(default_factory=list)


def note_path(*parts: str) -> str:
    return "/".join((PKM_PREFIX,) + tuple(p.replace(os.sep, "/") for p in parts))


def daily_note(day: date) -> str:
    """A daily note's path in the pkm folder: `vault/journals/fleeting/YYYY-MM-DD.md`."""
    return os.path.join(FLEETING_DIR, f"{day.isoformat()}.md")


def weekly_note(day: date) -> str:
    """The weekly note of `day`'s vault week in the pkm folder, under the Journals plugin's name:
    `vault/journals/weekly/2026-W43.md`.
    """
    return os.path.join(WEEKLY_DIR, f"{vault_week_names(day)[0]}.md")


def read_note(path: str) -> Optional[str]:
    """The file's text, or None when there is no such file."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return None


def clip(text: str, limit: int = NOTE_CHAR_LIMIT) -> Tuple[str, int]:
    """`text` cut after its last whole line within `limit` characters, and how many lines were left out."""
    lines = text.rstrip("\n").split("\n")
    kept, size = [], 0
    for n, line in enumerate(lines):
        if size + len(line) > limit and kept:
            return "\n".join(kept), len(lines) - n
        kept.append(line)
        size += len(line) + 1
    return "\n".join(kept), 0


def focus_section(text: str) -> Optional[List[str]]:
    """The lines under a note's "Next week's focus" heading, as written, until the next heading
    of the same or a higher level. Blank lines are left out. None when there is no such heading.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        heading = _HEADING.match(line)
        if not heading or not _FOCUS.search(heading.group(2)):
            continue
        level, items = len(heading.group(1)), []
        for item in lines[i + 1:]:
            nested = _HEADING.match(item)
            if nested and len(nested.group(1)) <= level:
                break
            if item.strip():
                items.append(item.rstrip())
        return items
    return None


def is_template_focus(lines: List[str]) -> bool:
    """Whether a focus section is still the weekly template's placeholder items: A, B, C."""
    return [re.sub(r"^[\s*+\-\d.]+", "", line).strip() for line in lines] == _PLACEHOLDER_ITEMS


def read_weekly_focus(pkm_dir: str, today: date, report: Report) -> WeeklyFocus:
    """This week's focus: last vault week's "Next week's focus", as the daily template embeds it.

    When that section is missing, empty or still the template's A/B/C, the
    current week's note is used instead: earlier weekly reviews wrote the coming
    week's focus into that week's note. A weekly note without the heading is
    a problem in `report`: the note changed shape.
    """
    looked = []
    for day in (today - timedelta(days=7), today):
        source = _weekly_focus_source(pkm_dir, day, report)
        looked.append(source)
        if source.state == "set":
            return WeeklyFocus(items=source.lines, source=source.name, looked=looked)
    return WeeklyFocus(items=[], looked=looked)


def _weekly_focus_source(pkm_dir: str, day: date, report: Report) -> FocusSource:
    names = vault_week_names(day)
    for name in names:
        path = os.path.join(pkm_dir, WEEKLY_DIR, f"{name}.md")
        text = read_note(path)
        if text is None:
            continue
        lines = focus_section(text)
        report.read(path, records=len(lines or []))
        if lines is None:
            report.problem(path, "no \"Next week's focus\" heading")
            return FocusSource(name, path, "no heading")
        if not lines:
            return FocusSource(name, path, "empty")
        return FocusSource(name, path, "template" if is_template_focus(lines) else "set", lines)
    return FocusSource(names[0], os.path.join(pkm_dir, WEEKLY_DIR, f"{names[0]}.md"), "no note")


def with_journal_keys(text: str, journal: str, day: date) -> str:
    """`text` as written, with the Journals plugin's keys added to its frontmatter, or in a
    new one: `journal` ("fleeting", "weekly") and `journal-date`, the first day of the note's
    period (the day, or the week's Sunday).

    Keys already there stay as they are. Every other line, Templater syntax (`<% ... %>`)
    included, stays as written, as in the notes the plugin creates.
    """
    keys = list(zip(_JOURNAL_KEYS, (journal, day.isoformat())))
    lines = text.split("\n")
    end = _frontmatter_end(lines)
    if end is not None:
        present = {l.partition(":")[0].strip() for l in lines[1:end]}
        lines[end:end] = [f"{k}: {v}" for k, v in keys if k not in present]
        return "\n".join(lines)
    return "\n".join(["---"] + [f"{k}: {v}" for k, v in keys] + ["---"] + lines)


def is_untouched(text: str, template: str) -> bool:
    """Whether a daily note is still what a new note starts as: `template` with the Journals
    plugin's keys (`with_journal_keys`), nothing written in it.

    Line endings, trailing whitespace and the plugin's keys, whatever their date or place in the
    frontmatter, don't count as writing. Any other change does, a frontmatter value such as
    `energy_level: 6` included.
    """
    return _as_compared(text) == _as_compared(template)


def _as_compared(text: str) -> List[str]:
    """`text`'s lines as `is_untouched` compares them: no trailing whitespace, no blank lines at
    the end, and no plugin keys (nor the frontmatter, when they were all it held).
    """
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    end = _frontmatter_end(lines)
    if end is not None:
        kept = [line for line in lines[1:end] if line.partition(":")[0].strip() not in _JOURNAL_KEYS]
        lines = (["---", *kept, "---"] if kept else []) + lines[end + 1:]
    while lines and not lines[-1]:
        lines.pop()
    return lines


def _frontmatter_end(lines: List[str]) -> Optional[int]:
    """The index of the `---` line closing the frontmatter; None when the text has no frontmatter."""
    if lines[0].strip() != "---":
        return None
    return next((j for j in range(1, len(lines)) if lines[j].strip() == "---"), None)
