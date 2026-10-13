"""The precondition library: cheap, deterministic answers to "is there work?".

A prompt schedule declares one of these in `agent.json` instead of asking the
model to "terminate silently if empty":

    "precondition": {"type": "files_exist", "paths": ["pkm/inbox/**"]}

Rules every precondition here follows:

- **No model, no paid API.** Filesystem stats, local SQLite reads, a regex over
  a local file. If the only way to know is to ask a model or an external
  service, the schedule uses `always` with a stated reason instead.
  One narrow exception: `sleep_logged_today` makes a free, read-only,
  deterministic Google Health read (no model). It checks its local
  once-a-day gate first and fails *closed* on any API error, so a broken
  connection never turns into a paid agent turn.
- **Deterministic.** The same state gives the same answer.
- **Validated at load.** A misspelt type or a missing parameter rejects the
  schedule when it is loaded, not at 3am when it would have fired.

Relative paths resolve against the repository root, the same way the agents'
`filesystem` tool sees `pkm/...`.
"""
import glob
import json
import os
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, List, Optional

from core.scheduler.spec import ScheduleContext, WorkDecision

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class PreconditionError(ValueError):
    """A precondition declaration that cannot be built."""


# --------------------------------------------------------------------------
# Filesystem helpers. Public because scripts reuse them in their own has_work().
# --------------------------------------------------------------------------

def resolve_path(path: str, root: str = PROJECT_ROOT) -> str:
    expanded = os.path.expanduser(str(path))
    return expanded if os.path.isabs(expanded) else os.path.join(root, expanded)


def _is_hidden(name: str) -> bool:
    return name.startswith(".")


def _iter_matches(patterns: Iterable[str], root: str = PROJECT_ROOT):
    for pattern in patterns:
        for match in sorted(glob.glob(resolve_path(pattern, root), recursive=True)):
            yield match


def _walk_entries(path: str):
    """Yields (path, is_dir) for `path` and, if a directory, everything under it.

    Directories are yielded too: deleting a file changes its parent's mtime and
    nothing else, and a deletion is a change a sync has to see.
    """
    if os.path.isdir(path):
        yield path, True
        for current, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not _is_hidden(d)]
            for d in dirs:
                yield os.path.join(current, d), True
            for f in files:
                if not _is_hidden(f):
                    yield os.path.join(current, f), False
    elif os.path.exists(path):
        yield path, False


def first_changed_path(
    patterns: Iterable[str],
    since: Optional[float],
    root: str = PROJECT_ROOT,
    suffixes: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """The first matching file or directory modified after `since`, else None.

    `suffixes` restricts which *files* count (directories always do, since a
    deletion shows up only as its parent's mtime).
    """
    wanted = tuple(suffixes) if suffixes else None
    for match in _iter_matches(patterns, root):
        for path, is_dir in _walk_entries(match):
            if wanted and not is_dir and not path.endswith(wanted):
                continue
            try:
                if os.path.getmtime(path) > since:
                    return path
            except OSError:
                continue
    return None


def first_existing_file(patterns: Iterable[str], root: str = PROJECT_ROOT) -> Optional[str]:
    """The first non-hidden regular file matching `patterns`, else None."""
    for match in _iter_matches(patterns, root):
        for path, is_dir in _walk_entries(match):
            if not is_dir and not _is_hidden(os.path.basename(path)):
                return path
    return None


def _rel(path: str, root: str = PROJECT_ROOT) -> str:
    try:
        return os.path.relpath(path, root)
    except ValueError:
        return path


# --------------------------------------------------------------------------
# Preconditions
# --------------------------------------------------------------------------

class Precondition(ABC):
    type_name: str = ""

    def __init__(self, config: Dict[str, Any]):
        self.config = dict(config)
        self.validate()

    def validate(self) -> None:
        """Raises PreconditionError for a declaration that cannot work."""

    @abstractmethod
    def evaluate(self, ctx: ScheduleContext) -> WorkDecision:
        ...

    def _require_str(self, key: str) -> str:
        value = self.config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise PreconditionError(f"'{self.type_name}' requires a non-empty '{key}'")
        return value

    def _require_str_list(self, key: str) -> List[str]:
        value = self.config.get(key)
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list) or not value or not all(
            isinstance(v, str) and v.strip() for v in value
        ):
            raise PreconditionError(
                f"'{self.type_name}' requires '{key}' as a non-empty list of strings"
            )
        return value

    def _check_keys(self, allowed: Iterable[str]) -> None:
        unknown = sorted(set(self.config) - set(allowed) - {"type"})
        if unknown:
            raise PreconditionError(
                f"'{self.type_name}' does not take: {', '.join(unknown)}"
            )


class Always(Precondition):
    """Run every time. Allowed only with a stated reason, so it stays honest."""
    type_name = "always"

    def validate(self):
        self._check_keys(["reason"])
        self.reason = self._require_str("reason")

    def evaluate(self, ctx):
        return WorkDecision(True, f"always: {self.reason}")


class FilesChangedSince(Precondition):
    """A matching file or directory changed since the last successful run."""
    type_name = "files_changed_since"

    def validate(self):
        self._check_keys(["paths"])
        self.paths = self._require_str_list("paths")

    def evaluate(self, ctx):
        if ctx.last_success_at is None:
            return WorkDecision(True, "no previous successful run")
        changed = first_changed_path(self.paths, ctx.last_success_at)
        if changed:
            return WorkDecision(True, f"{_rel(changed)} changed since last success")
        return WorkDecision(False, f"nothing under {', '.join(self.paths)} changed since last success")


class FilesExist(Precondition):
    """At least one (non-hidden) file matches — e.g. an inbox that is not empty."""
    type_name = "files_exist"

    def validate(self):
        self._check_keys(["paths"])
        self.paths = self._require_str_list("paths")

    def evaluate(self, ctx):
        found = first_existing_file(self.paths)
        if found:
            return WorkDecision(True, f"{_rel(found)} exists")
        return WorkDecision(False, f"no files match {', '.join(self.paths)}")


class FileMatches(Precondition):
    """A file exists and contains a regex match — e.g. a queued request block."""
    type_name = "file_matches"

    def validate(self):
        self._check_keys(["path", "pattern"])
        self.path = self._require_str("path")
        pattern = self._require_str("pattern")
        try:
            self.regex = re.compile(pattern)
        except re.error as e:
            raise PreconditionError(f"'file_matches' pattern does not compile: {e}")

    def evaluate(self, ctx):
        full = resolve_path(self.path)
        if not os.path.isfile(full):
            return WorkDecision(False, f"{self.path} does not exist")
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if self.regex.search(content):
            return WorkDecision(True, f"{self.path} matches /{self.regex.pattern}/")
        return WorkDecision(False, f"{self.path} has no match for /{self.regex.pattern}/")


class JsonNonEmpty(Precondition):
    """A JSON file has at least one non-empty value under the listed keys."""
    type_name = "json_non_empty"

    def validate(self):
        self._check_keys(["path", "keys"])
        self.path = self._require_str("path")
        self.keys = self._require_str_list("keys")

    def evaluate(self, ctx):
        full = resolve_path(self.path)
        if not os.path.isfile(full):
            return WorkDecision(False, f"{self.path} does not exist")
        with open(full, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return WorkDecision(True, f"{self.path} is not an object; letting the agent judge")
        filled = [k for k in self.keys if data.get(k)]
        if filled:
            return WorkDecision(True, f"{self.path} has entries under {', '.join(filled)}")
        return WorkDecision(False, f"{self.path} is empty under {', '.join(self.keys)}")


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
        from core.knowledge.tasks.db import get_connection, get_db_path

        db_path = self.config.get("db_path") or get_db_path()
        if not os.path.exists(db_path):
            return WorkDecision(False, f"task store {db_path} does not exist")
        conn = get_connection(db_path)
        try:
            rows = conn.execute(
                "SELECT priority, tags FROM tasks WHERE status = 'todo'"
            ).fetchall()
        finally:
            conn.close()

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
        from core.knowledge.projects.db import get_connection, get_db_path

        db_path = self.config.get("db_path") or get_db_path()
        if not os.path.exists(db_path):
            return WorkDecision(False, f"project store {db_path} does not exist")
        conn = get_connection(db_path)
        try:
            count = conn.execute(
                "SELECT count(*) FROM projects WHERE lower(status) = ?", (self.status,)
            ).fetchone()[0]
        finally:
            conn.close()
        if count:
            return WorkDecision(True, f"{count} {self.status} project(s)")
        return WorkDecision(False, f"no {self.status} projects")


class SleepLoggedToday(Precondition):
    """Today's sleep has synced (the user is awake) and this schedule has not
    already succeeded today.

    Evaluated cheapest-first:

    1. Once-a-day gate: `last_success_at` on today's local date -> no. Local
       only; no network call. It is read from the `schedule_state` table in
       `sessions/memory.db`, so it survives restarts. It advances only on a
       *successful* run, so a failed push is retried on the next tick.
    2. Wake check: a Google Health sleep session whose wake date is today,
       not flagged as a nap, and at least `MIN_MINUTES_ASLEEP` long (filters
       out naps and partial syncs).

    Fails closed: any error reading Google Health is a "no" with the error as
    the reason. The runner fails open on exceptions, which here would mean an
    agent turn on every tick whenever the API is down.
    """
    type_name = "sleep_logged_today"
    MIN_MINUTES_ASLEEP = 180

    def validate(self):
        self._check_keys([])

    @staticmethod
    def _tz():
        from core.util.time_util import get_local_timezone
        return get_local_timezone()

    @staticmethod
    def _read_sleep() -> Dict[str, Any]:
        """Today's sleep via the google_health tool's own reader. Patched in tests."""
        from tools.google_health import GoogleHealthClient, read_sleep
        return read_sleep(GoogleHealthClient(), {"days": 1})

    def evaluate(self, ctx):
        from datetime import datetime

        tz = self._tz()
        today = datetime.fromtimestamp(ctx.now, tz).date()
        if ctx.last_success_at is not None:
            if datetime.fromtimestamp(ctx.last_success_at, tz).date() == today:
                return WorkDecision(False, "already triggered today")

        try:
            data = self._read_sleep() or {}
        except Exception as e:  # fail closed: never pay for a broken check
            return WorkDecision(False, f"could not read sleep data ({type(e).__name__}: {e})")

        today_iso = today.isoformat()
        sessions = [s for s in data.get("sessions") or [] if s.get("wake_date") == today_iso]
        # Only drop sessions explicitly flagged as naps; some sources omit the flag.
        sessions = [s for s in sessions if s.get("main_sleep") is not False]
        sessions = [s for s in sessions if (s.get("minutes_asleep") or 0) >= self.MIN_MINUTES_ASLEEP]
        if not sessions:
            return WorkDecision(False, f"no qualifying sleep session ending {today_iso} yet")

        latest = max(sessions, key=lambda s: s.get("end") or "")
        woke = (latest.get("end") or "")[-5:] or "?"
        hours = round((latest.get("minutes_asleep") or 0) / 60, 1)
        return WorkDecision(True, f"woke {woke}, {hours}h asleep")


REGISTRY = {
    cls.type_name: cls
    for cls in (
        Always,
        FilesChangedSince,
        FilesExist,
        FileMatches,
        JsonNonEmpty,
        TaskListNonEmpty,
        ProjectListNonEmpty,
        SleepLoggedToday,
    )
}


def build_precondition(config: Any) -> Precondition:
    """Builds a precondition from its `agent.json` declaration, or raises."""
    if not isinstance(config, dict):
        raise PreconditionError("'precondition' must be an object like {\"type\": ...}")
    type_name = config.get("type")
    if not type_name:
        raise PreconditionError("'precondition' is missing 'type'")
    cls = REGISTRY.get(type_name)
    if cls is None:
        raise PreconditionError(
            f"unknown precondition type '{type_name}' (known: {', '.join(sorted(REGISTRY))})"
        )
    return cls(config)
