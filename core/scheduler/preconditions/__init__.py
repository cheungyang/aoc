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
  The scans (`Scan` subclasses: `market_screen`, `ha_audit`, ...) may make
  the same read-only calls the agent's own first step would; they also fail
  closed, and pass what they found into the prompt.
- **Deterministic.** The same state gives the same answer.
- **Validated at load.** A misspelt type or a missing parameter rejects the
  schedule when it is loaded, not at 3am when it would have fired.

Modules:

    base.py     Precondition, PreconditionError, always
    files.py    files_changed_since, files_exist, file_matches, json_non_empty,
                and the filesystem helpers scripts reuse
    stores.py   task_list_non_empty, project_list_non_empty
    sleep.py    sleep_logged_today
    scan.py     Scan, the base of the domain scans
    <type>.py   one module per scan, named after its type
"""
from typing import Any

from core.scheduler.preconditions.base import Always, Precondition, PreconditionError
from core.scheduler.preconditions.files import (
    PROJECT_ROOT,
    FileMatches,
    FilesChangedSince,
    FilesExist,
    JsonNonEmpty,
    first_changed_path,
    first_existing_file,
    resolve_path,
)
from core.scheduler.preconditions.scan import Scan
from core.scheduler.preconditions.sleep import SleepLoggedToday
from core.scheduler.preconditions.stores import ProjectListNonEmpty, TaskListNonEmpty, is_untriaged
from core.scheduler.preconditions.market_screen import MarketScreen
from core.scheduler.preconditions.property_screen import PropertyScreen
from core.scheduler.preconditions.credit_windows import CreditWindows
from core.scheduler.preconditions.ha_audit import HaAudit
from core.scheduler.preconditions.morning_briefing import MorningBriefing
from core.scheduler.preconditions.projects_attention import ProjectsAttention

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
        MarketScreen,
        PropertyScreen,
        CreditWindows,
        HaAudit,
        MorningBriefing,
        ProjectsAttention,
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
