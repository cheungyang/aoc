"""Preconditions over the local task and project stores (tasks.db, projects.db)."""
import json
from typing import Any, List

from core.scheduler.preconditions.base import Precondition, PreconditionError
from core.scheduler.spec import WorkDecision


def _normalise_tags(raw: Any) -> List[str]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            raw = raw.split()
    if not isinstance(raw, list):
        return []
    return [str(t).lstrip("#").lower() for t in raw]


def is_untriaged(priority: Any, tags: Any) -> bool:
    """Mirrors the triage_tasks skill: no priority AND missing an #a/ or a #p/ tag."""
    if priority not in (None, ""):
        return False
    normalised = _normalise_tags(tags)
    has_action = any(t.startswith("a/") for t in normalised)
    has_project = any(t.startswith("p/") for t in normalised)
    return not (has_action and has_project)


class TaskListNonEmpty(Precondition):
    """The task store (tasks.db) holds matching open tasks."""
    type_name = "task_list_non_empty"
    FILTERS = ("todo", "untriaged")

    def validate(self):
        self._check_keys(["filter", "db_path"])
        self.filter = str(self.config.get("filter") or "todo").lower()
        if self.filter not in self.FILTERS:
            raise PreconditionError(
                f"'task_list_non_empty' filter must be one of: {', '.join(self.FILTERS)}"
            )

    def evaluate(self, ctx):
        from core.storage import StoreMissing
        from core.storage.tasks import TasksStore

        store = TasksStore(self.config.get("db_path"))
        try:
            with store.read_only() as conn:
                rows = store.select("tasks", {"status": "todo"}, columns=("priority", "tags"), conn=conn)
        except StoreMissing:
            return WorkDecision(False, f"task store {store.path} does not exist")

        if self.filter == "untriaged":
            count = sum(1 for r in rows if is_untriaged(r["priority"], r["tags"]))
        else:
            count = len(rows)
        if count:
            return WorkDecision(True, f"{count} {self.filter} task(s)")
        return WorkDecision(False, f"no {self.filter} tasks")


class ProjectListNonEmpty(Precondition):
    """The project store (projects.db) holds projects with the given status."""
    type_name = "project_list_non_empty"

    def validate(self):
        from core.knowledge.projects.parser import STATUS_MAP

        self._check_keys(["status", "db_path"])
        self.status = str(self.config.get("status") or "executing").lower()
        # A misspelt status would otherwise pass validation and then skip
        # forever with "no <typo> projects".
        known = sorted(set(STATUS_MAP.values()))
        if self.status not in known:
            raise PreconditionError(
                f"'{self.type_name}' status '{self.status}' is not one of: {', '.join(known)}"
            )

    def evaluate(self, ctx):
        from core.storage import StoreMissing
        from core.storage.projects import ProjectsStore

        store = ProjectsStore(self.config.get("db_path"))
        try:
            with store.read_only() as conn:
                count = store.count_with_status(self.status, conn=conn)
        except StoreMissing:
            return WorkDecision(False, f"project store {store.path} does not exist")
        if count:
            return WorkDecision(True, f"{count} {self.status} project(s)")
        return WorkDecision(False, f"no {self.status} projects")
