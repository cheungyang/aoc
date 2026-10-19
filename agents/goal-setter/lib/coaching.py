"""Coaching history: when Grace last coached a project, and what she noted.

Two records, both Grace's:
- the dossier, pkm/wiki/project-coaching/<project>.md, whose frontmatter
  `last_coached` she sets at each coaching prep or outreach;
- the project note, where each agreed session adds a `### YYYY-MM-DD from Grace`
  section (newest first, after the dataview blocks).
The later of the two is when the project was last coached.

Nothing is guessed: a dossier without a readable `last_coached`, a session
heading whose date doesn't read, or a note that can't be opened goes in the
report, with the raw lines, and the history covers the rest.
"""
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Optional, Tuple

from core.knowledge.projects.parser import extract_frontmatter
from core.tooling import Report

DOSSIER_DIR = os.path.join("wiki", "project-coaching")
NOTES_LIMIT = 1200  # characters of a dossier or session handed to the model
# A heading whose first word is date-like and is followed by "from Grace": a session
# when that word is a YYYY-MM-DD date, else a heading reported as unreadable.
_SESSION_HEADING = re.compile(r"^(#{1,6})[ \t]+([0-9][0-9./-]*)[ \t]+from Grace\b.*$", re.MULTILINE)
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_STATUS_LINE = re.compile(r"^\*\*Status:\*\*.*\n?", re.MULTILINE)


@dataclass(frozen=True)
class Dossier:
    path: str                      # relative to the PKM root
    last_coached: Optional[date]
    strategy: str                  # the body, minus frontmatter and the status line
    project_name: str = ""
    problem: str = ""              # why last_coached is unknown, when no line says it
    unreadable: Tuple[str, ...] = ()  # frontmatter lines that couldn't be read, raw


@dataclass(frozen=True)
class Sessions:
    """The `### YYYY-MM-DD from Grace` sections of a project note."""
    latest: Optional[date] = None
    notes: str = ""                # the latest session's text, trimmed
    count: int = 0                 # sessions with a readable date
    unreadable: Tuple[str, ...] = ()  # session headings whose date doesn't read, raw


@dataclass(frozen=True)
class CoachingHistory:
    dossier_path: str              # the existing dossier, or where a new one goes
    dossier: Optional[Dossier]
    session_date: Optional[date]   # the latest "from Grace" section of the note
    session_notes: str

    @property
    def last_coached(self) -> Optional[date]:
        dates = [d for d in (self.dossier and self.dossier.last_coached, self.session_date) if d]
        return max(dates) if dates else None

    def as_dict(self, today: date) -> Dict[str, Any]:
        last = self.last_coached
        result = {
            "last_coached": last.isoformat() if last else None,
            "days_since_coached": (today - last).days if last else None,
            "dossier": os.path.join("pkm", self.dossier_path),
        }
        if self.dossier and self.dossier.strategy:
            result["dossier_notes"] = self.dossier.strategy
        if self.session_date:
            result["last_session"] = {"date": self.session_date.isoformat(), "notes": self.session_notes}
        return result


def read_dossier(text: str, path: str = "") -> Dossier:
    """A dossier's text, read: `project_name` and `last_coached` from its frontmatter, and its notes."""
    frontmatter, body = extract_frontmatter(text)
    strategy = _trim(_STATUS_LINE.sub("", body))
    if body == text:
        return Dossier(path, None, strategy, problem="no frontmatter, so no last_coached date")
    head = text[: len(text) - len(body)].splitlines()
    while head and not head[-1].strip():
        head.pop()
    block = head[1:-1]  # the lines between the --- fences
    if not frontmatter:  # YAML that doesn't read as key: value pairs
        lines = tuple(line for line in block if line.strip())
        if not lines:
            return Dossier(path, None, strategy, problem="empty frontmatter, so no last_coached date")
        return Dossier(path, None, strategy, unreadable=lines)
    name = str(frontmatter.get("project_name") or "").strip()
    value = frontmatter.get("last_coached")
    if value is None or str(value).strip() == "":
        return Dossier(path, None, strategy, name, problem="no last_coached date in its frontmatter")
    day = _as_date(value)
    if day is None:
        line = next((raw for raw in block if raw.strip().startswith("last_coached")), f"last_coached: {value}")
        return Dossier(path, None, strategy, name, unreadable=(line,))
    return Dossier(path, day, strategy, name)


def read_sessions(text: str) -> Sessions:
    """A project note's sessions: the newest by date, not position, with its text up to
    the next heading of the same or a higher level."""
    found, unreadable = [], []
    for match in _SESSION_HEADING.finditer(text):
        day = _as_iso_date(match.group(2))
        if day:
            found.append((day, match))
        else:
            unreadable.append(match.group(0).strip())
    if not found:
        return Sessions(unreadable=tuple(unreadable))
    day, match = max(found, key=lambda item: item[0])
    level = len(match.group(1))
    rest = text[match.end():]
    end = re.search(rf"^#{{1,{level}}}[ \t]", rest, re.MULTILINE)
    return Sessions(day, _trim(rest[: end.start()] if end else rest), len(found), tuple(unreadable))


def load_dossiers(pkm_dir: str, report: Report) -> Dict[str, Dossier]:
    """Every dossier, keyed by its normalised project name and file name."""
    folder = os.path.join(pkm_dir, DOSSIER_DIR)
    dossiers: Dict[str, Dossier] = {}
    if not os.path.isdir(folder):
        return dossiers
    names = [n for n in sorted(os.listdir(folder)) if n.endswith(".md") and not n.startswith(".")]
    for filename in names:
        path = os.path.join(DOSSIER_DIR, filename)
        try:
            with open(os.path.join(folder, filename), encoding="utf-8", errors="replace") as f:
                dossier = read_dossier(f.read(), path)
        except OSError as e:
            dossier = Dossier(path, None, "", problem=f"can't be read ({e.strerror or e})")
        for key in (dossier.project_name, filename[:-3]):
            if key:
                dossiers.setdefault(_normalise(key), dossier)
    report.read(folder, records=len(names))
    return dossiers


def coaching_history(project: Dict[str, Any], pkm_dir: str, dossiers: Dict[str, Dossier],
                     report: Report) -> CoachingHistory:
    """The project's dossier (from `load_dossiers`) and its note's latest session.

    What couldn't be read of either goes in `report`."""
    note = project.get("file_path") or ""
    note_name = os.path.splitext(os.path.basename(note))[0]
    dossier = dossiers.get(_normalise(project.get("name") or "")) or dossiers.get(_normalise(note_name))
    # A new dossier is named after the note, which survives title edits.
    path = dossier.path if dossier else os.path.join(DOSSIER_DIR, f"{note_name or project.get('name')}.md")
    if dossier:
        full = os.path.join(pkm_dir, dossier.path)
        report.read(full)
        if dossier.problem:
            report.problem(full, dossier.problem)
        report.unparsed(full, dossier.unreadable)
    sessions = note_sessions(os.path.join(pkm_dir, note), report) if note else Sessions()
    return CoachingHistory(path, dossier, sessions.latest, sessions.notes)


def note_sessions(note_path: str, report: Report) -> Sessions:
    """The sessions in a project note. A note that can't be opened is an issue (partial)."""
    try:
        with open(note_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as e:
        report.problem(note_path, f"can't be read ({e.strerror or e}); its coaching sessions are unknown")
        return Sessions()
    sessions = read_sessions(text)
    report.read(note_path, records=sessions.count)
    report.unparsed(note_path, sessions.unreadable)
    return sessions


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10]) if value else None
    except ValueError:
        return None


def _as_iso_date(token: str) -> Optional[date]:
    """`token` as a date when it is exactly YYYY-MM-DD, else None."""
    if not _ISO_DATE.fullmatch(token):
        return None
    try:
        return date.fromisoformat(token)
    except ValueError:
        return None


def _normalise(name: str) -> str:
    return " ".join(str(name).lower().split())


def _trim(text: str) -> str:
    """Without Obsidian %% comments (task ids), cut to NOTES_LIMIT at a word break."""
    text = re.sub(r"[ \t]*%%.*?%%", "", text, flags=re.DOTALL).strip()
    if len(text) <= NOTES_LIMIT:
        return text
    cut = text[:NOTES_LIMIT]
    return cut[: max(cut.rfind("\n"), cut.rfind(" "), NOTES_LIMIT // 2)].rstrip() + " …"
