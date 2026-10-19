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
  closed, pass what they found into the prompt, and post what they couldn't
  read in the agent's channel.
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

The scans themselves are agent-specific, so they live with their agent:
`agents/<agent_id>/preconditions/<type>.py` (see core/loaders/agent_code.py).
A schedule may use the types here plus its own agent's scans.
"""
import importlib
import inspect
import sys
from typing import Any, Dict, Optional

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


_agent_types: Dict[str, Dict[str, type]] = {}


def agent_preconditions(agent_id: str) -> Dict[str, type]:
    """The agent's own precondition types: its scans in `agents/<agent_id>/preconditions/`.

    A module that fails to import is logged and skipped, so one broken scan
    rejects only the schedules that use it ("unknown precondition type").
    """
    from core.loaders.agent_code import modules

    if agent_id not in _agent_types:
        found: Dict[str, type] = {}
        for path in modules(agent_id, "preconditions").values():
            try:
                module = importlib.import_module(path)
            except Exception as e:
                print(f"[Preconditions] could not import {path}: {type(e).__name__}: {e}", file=sys.stderr)
                continue
            for value in vars(module).values():
                if (isinstance(value, type) and issubclass(value, Precondition) and value.__module__ == path
                        and value.type_name and not inspect.isabstract(value)):
                    found[value.type_name] = value
        _agent_types[agent_id] = found
    return _agent_types[agent_id]


def clear_agent_cache() -> None:
    """Forgets discovered scans. For tests and hot reload."""
    _agent_types.clear()


def build_precondition(config: Any, agent_id: Optional[str] = None) -> Precondition:
    """Builds a precondition from its `agent.json` declaration, or raises.

    `agent_id` adds that agent's own scans to the types it may use.
    """
    if not isinstance(config, dict):
        raise PreconditionError("'precondition' must be an object like {\"type\": ...}")
    type_name = config.get("type")
    if not type_name:
        raise PreconditionError("'precondition' is missing 'type'")
    own = agent_preconditions(agent_id) if agent_id else {}
    cls = REGISTRY.get(type_name) or own.get(type_name)
    if cls is None:
        raise PreconditionError(
            f"unknown precondition type '{type_name}' (known: {', '.join(sorted(set(REGISTRY) | set(own)))})"
        )
    return cls(config)
