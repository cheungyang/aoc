"""The work the morning plan chooses from: active projects with their top open tasks,
and open tasks that are due soon or have slipped.

The same reads list_impactful_actions used to make by hand (project_query for the
year's active projects, then task_query per alias), done once in code and
sorted. Recurring chores are left out: `recurring_chores` judges those.
A task whose date isn't a real date is returned raw in the report, never dropped.
"""
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import List, Optional, Tuple

from agents.day_planner.lib.day_calendar import relative_phrase, short_date
from agents.day_planner.lib.task_cache import RECURRENCE, cache_path, display_title, read_day
from core.tooling import Report

ACTIVE_STATUSES = ("executing", "planning")
TASKS_PER_PROJECT = 3
DUE_SOON_DAYS = 7
DUE_LIST_LIMIT = 8
TITLE_LIMIT = 80
PROJECTS_DIR = "pkm/vault/projects/"

_OPEN_TASK_ORDER = (
    "priority_rank, due_date IS NULL, due_date, scheduled_date IS NULL, scheduled_date, title"
)


@dataclass
class Project:
    name: str
    priority: str
    status: str
    category: str
    aliases: List[str]
    file_path: str  # relative to the pkm folder, as tasks.db stores sources
    open_count: int = 0
    top_tasks: List[str] = field(default_factory=list)


def active_projects(projects: sqlite3.Connection, tasks: sqlite3.Connection, today: date,
                    report: Report) -> List[Project]:
    """This year's executing or planning projects, by priority, each with its top open tasks."""
    year = today.year
    rows = projects.execute(
        "SELECT name, status, priority, category, aliases, file_path FROM projects "
        "WHERE commitment_year = ? OR id IN (SELECT id FROM project_commitments WHERE year = ?) "
        "ORDER BY priority_rank, name",
        (year, year),
    ).fetchall()
    found, unreadable, task_count = [], [], 0
    for row in rows:
        status = project_status(row["status"])
        if status not in ACTIVE_STATUSES:
            continue
        project = Project(
            name=row["name"],
            priority=row["priority"] or "",
            status=status,
            category=row["category"] or "",
            aliases=[a if a.startswith("#") else f"#{a}" for a in _json_list(row["aliases"])],
            file_path=row["file_path"] or "",
        )
        open_tasks = _open_tasks(tasks, project)
        task_count += len(open_tasks)
        project.open_count = len(open_tasks)
        project.top_tasks = [_task_line(t, today, unreadable) for t in open_tasks[:TASKS_PER_PROJECT]]
        found.append(project)
    report.read(cache_path(projects), records=len(rows))
    report.read(cache_path(tasks), records=task_count)
    report.unparsed(cache_path(tasks), unreadable)
    report.trace(f"Projects committed to {year}: {len(rows)}; executing or planning: {len(found)}")
    return found


def format_projects(projects: List[Project]) -> str:
    if not projects:
        return "none"
    lines = []
    for p in projects:
        facts = [p.category, p.status, " ".join(p.aliases), f"{p.open_count} open"]
        file_name = p.file_path.rsplit("/", 1)[-1]
        if file_name != f"{p.name}.md":
            facts.append(f"file: {file_name}")
        lines.append(f"{p.priority} {p.name} · {' · '.join(f for f in facts if f)}".strip())
        lines.extend(f"  - {t}" for t in p.top_tasks)
    lines.append(f"Project files are {PROJECTS_DIR}<project name>.md unless a file is shown.")
    return "\n".join(lines)


def due_tasks(tasks: sqlite3.Connection, today: date, report: Report) -> List[str]:
    """Open tasks due within DUE_SOON_DAYS, or scheduled on or before today, soonest first."""
    horizon = today + timedelta(days=DUE_SOON_DAYS)
    rows = tasks.execute(
        "SELECT raw_title, priority, priority_rank, due_date, scheduled_date, tags FROM tasks "
        f"WHERE status = 'todo' AND raw_title NOT LIKE '%{RECURRENCE}%' "
        "AND ((due_date IS NOT NULL AND due_date <= ?) OR (scheduled_date IS NOT NULL AND scheduled_date <= ?))",
        (horizon.isoformat(), today.isoformat()),
    ).fetchall()
    dated, unreadable = [], []
    for row in rows:
        when = task_when(row["due_date"], row["scheduled_date"], horizon)
        if when is None:
            unreadable.append(row["raw_title"])
        else:
            dated.append((when, row))
    dated.sort(key=lambda item: (item[0][1], item[1]["priority_rank"] or 99))
    lines = []
    for (kind, on), row in dated[:DUE_LIST_LIMIT]:
        project = next((f"#{t}" for t in _json_list(row["tags"]) if t.startswith("p/")), "")
        title = " ".join(x for x in (row["priority"] or "", display_title(row["raw_title"], TITLE_LIMIT), project) if x)
        lines.append(f"{title}: {relative_phrase(kind, on, today)}")
    if len(dated) > DUE_LIST_LIMIT:
        lines.append(f"(+{len(dated) - DUE_LIST_LIMIT} more)")
    source = cache_path(tasks)
    report.read(source, records=len(dated), skipped=len(unreadable))
    report.unparsed(source, unreadable)
    report.trace(f"Open tasks due by {horizon.isoformat()} (today + {DUE_SOON_DAYS} days) or scheduled by "
                 f"{today.isoformat()}: {len(dated)}")
    return lines


def task_when(due: Optional[str], scheduled: Optional[str], horizon: date) -> Optional[Tuple[str, date]]:
    """The date a listed task is shown by: its due date when that falls by `horizon`, else its scheduled date.

    None when a date it carries isn't a real date.
    """
    due_day, scheduled_day = read_day(due), read_day(scheduled)
    if (due and due_day is None) or (scheduled and scheduled_day is None):
        return None
    if due_day is not None and due_day <= horizon:
        return "due", due_day
    return ("scheduled", scheduled_day) if scheduled_day is not None else None


def due_horizon(today: date) -> str:
    return short_date(today + timedelta(days=DUE_SOON_DAYS), today)


def project_status(raw: Optional[str]) -> str:
    """'executing' from 'executing' or 's/✊executing' (the sync keeps some tag prefixes)."""
    return re.sub(r"[^a-z]", "", (raw or "").rsplit("/", 1)[-1].lower())


def _open_tasks(tasks: sqlite3.Connection, project: Project) -> List[sqlite3.Row]:
    """The project's open tasks, best first; a task kept in two files counts once."""
    clauses, params = ["source = ?"], [project.file_path]
    for alias in project.aliases:
        clauses.append("tags LIKE ?")
        params.append(f'%"{alias.lstrip("#")}"%')
    rows = tasks.execute(
        "SELECT raw_title, priority, due_date, scheduled_date FROM tasks "
        f"WHERE status = 'todo' AND raw_title NOT LIKE '%{RECURRENCE}%' AND ({' OR '.join(clauses)}) "
        f"ORDER BY {_OPEN_TASK_ORDER}",
        params,
    ).fetchall()
    seen, unique = set(), []
    for row in rows:
        key = display_title(row["raw_title"], limit=500).lower()
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def _task_line(row: sqlite3.Row, today: date, unreadable: List[str]) -> str:
    line = " ".join(x for x in (row["priority"] or "", display_title(row["raw_title"], TITLE_LIMIT)) if x)
    for kind in ("due", "scheduled"):
        value = row[f"{kind}_date"]
        if value:
            day = read_day(value)
            if day is None:  # shown without a date; the raw line goes in the report
                unreadable.append(row["raw_title"])
                return line
            return f"{line}: {relative_phrase(kind, day, today)}"
    return line


def _json_list(value: Optional[str]) -> List[str]:
    try:
        items = json.loads(value) if value else []
    except (TypeError, ValueError):
        return []
    return [str(i) for i in items] if isinstance(items, list) else []
