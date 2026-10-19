"""Read-only views of projects.db and tasks.db for goal-setter's coaching tools.

The queries are core/knowledge's own (`query_projects_db`, `query_tasks_db`);
only the connection differs: SQLite's `mode=ro`, so a coaching read never
creates, migrates or write-locks a store, which `get_connection` + `init_db`
(the sync path) would.

Both stores are required: one that is missing or can't be read goes in the
report as missing, and the read stops with `DataProblem`.
"""
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.knowledge.projects.db import query_projects_db
from core.knowledge.tasks.db import query_tasks_db
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
    config = Config()
    projects = _rows(projects_db or config.projects_db_path, query_projects_db, report)
    tasks = _rows(tasks_db or config.tasks_db_path, query_tasks_db, report)
    report.raise_if_failed()
    return Snapshot(projects=projects, tasks=tasks, pkm_dir=pkm_dir or config.pkm_dir)


def _rows(path: str, query: Callable[..., List[Dict[str, Any]]], report: Report) -> List[Dict[str, Any]]:
    """Every row of one store; [] with a `missing` issue when it can't be read."""
    if not os.path.exists(path):
        report.missing(path, "not found; run its sync first")
        return []
    try:
        with _readonly(path) as conn:
            rows = query(conn, status="all", limit=-1, compact=False)
    except sqlite3.Error as e:
        report.missing(path, f"can't be read ({e})")
        return []
    report.read(path, records=len(rows))
    return rows


class _readonly:
    """A read-only connection to an existing store; FileNotFoundError when there is none."""

    def __init__(self, path: str):
        if not os.path.exists(path):
            raise FileNotFoundError(f"store {path} does not exist; run its sync first")
        self.conn = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row

    def __enter__(self) -> sqlite3.Connection:
        return self.conn

    def __exit__(self, *exc) -> None:
        self.conn.close()
