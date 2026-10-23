"""Read-only views of projects.db and tasks.db for goal-setter's coaching tools.

The queries are the stores' own (`ProjectsStore.search`, `TasksStore.search`);
only the connection differs: `read_only()`, SQLite's `mode=ro`, so a coaching
read never creates, migrates or write-locks a store, which `connect()` +
`init()` (the sync path) would.

Both stores are required: one that is missing or can't be read goes in the
report as missing, and the read stops with `DataProblem`.
"""
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

from core.storage import StoreMissing
from core.storage.projects import ProjectsStore
from core.storage.tasks import TasksStore
from core.tooling import Report
from core.util.config import Config


@dataclass(frozen=True)
class Snapshot:
    """Every project and task row the coaching tools look at, read once."""
    projects: List[Dict[str, Any]]
    tasks: List[Dict[str, Any]]
    pkm_dir: str


def load_snapshot(
    report: Report, pkm_dir: Optional[str] = None, projects_db: Optional[str] = None, tasks_db: Optional[str] = None
) -> Snapshot:
    """Both stores, read once. Raises DataProblem when one is missing or unreadable."""
    projects = _rows(ProjectsStore(projects_db), report)
    tasks = _rows(TasksStore(tasks_db), report)
    report.raise_if_failed()
    return Snapshot(projects=projects, tasks=tasks, pkm_dir=pkm_dir or Config().pkm_dir)


def _rows(store: Union[ProjectsStore, TasksStore], report: Report) -> List[Dict[str, Any]]:
    """Every row of one store; [] with a `missing` issue when it can't be read."""
    try:
        with store.read_only() as conn:
            rows = store.search(status="all", limit=-1, compact=False, conn=conn)
    except StoreMissing:
        report.missing(store.path, "not found; run its sync first")
        return []
    except sqlite3.Error as e:
        report.missing(store.path, f"can't be read ({e})")
        return []
    report.read(store.path, records=len(rows))
    return rows
