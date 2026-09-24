"""schedule_validate: checks `agent.json` schedule entries before they are saved.

Backed by the exact validator the scheduler runs at load time
(`core.scheduler.registry`), so "OK" here means the scheduler will register the
entry, and every "REJECTED" reason is the one it would log.
"""
import json
import os
from typing import Any, List, Optional, Tuple

from langchain_core.tools import tool

from core.util import format_tool_response

TOOL_NAME = "schedule_validate"
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _extract_entries(data: Any) -> Tuple[Optional[List[Any]], Optional[str]]:
    """A schedules list from a list, a single entry, or a whole agent.json."""
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict):
        if "schedules" in data:
            schedules = data.get("schedules")
            if not isinstance(schedules, list):
                return None, "'schedules' must be a list"
            return schedules, None
        return [data], None
    return None, "expected a schedule entry, a list of entries, or an agent.json object"


def _load_from_disk(agent_id: str) -> Tuple[Optional[List[Any]], Optional[str]]:
    path = os.path.join(PROJECT_ROOT, "agents", agent_id, "agent.json")
    if not os.path.isfile(path):
        return None, f"agents/{agent_id}/agent.json does not exist"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return None, f"cannot read agents/{agent_id}/agent.json: {e}"
    return _extract_entries(data)


def validate_schedules(agent_id: str, schedules_json: str = "") -> Tuple[str, str]:
    """(payload, errors) for the tool response. errors is "None" when all pass."""
    from core.scheduler.registry import build_spec, ScheduleValidationError

    if not agent_id or not str(agent_id).strip():
        return "", "Error: 'agent_id' is required."
    agent_id = str(agent_id).strip()

    if schedules_json and schedules_json.strip():
        try:
            data = json.loads(schedules_json)
        except json.JSONDecodeError as e:
            return "", f"Error: schedules_json is not valid JSON: {e}"
        entries, problem = _extract_entries(data)
    else:
        entries, problem = _load_from_disk(agent_id)
    if problem:
        return "", f"Error: {problem}"
    if not entries:
        return "No schedules to validate.", "None"

    lines = []
    rejected = 0
    for index, entry in enumerate(entries):
        cron = entry.get("cron") if isinstance(entry, dict) else None
        label = f"schedules[{index}] (cron {cron!r})"
        try:
            spec = build_spec(agent_id, entry)
        except ScheduleValidationError as e:
            rejected += 1
            lines.append(f"REJECTED {label}:")
            lines.extend(f"  - {reason}" for reason in e.errors)
            continue
        lines.append(f"OK {label}: {spec.describe()}")

    summary = f"{len(entries) - rejected}/{len(entries)} schedule(s) valid."
    payload = "\n".join([summary] + lines)
    errors = "None" if not rejected else (
        f"{rejected} schedule(s) would be rejected by the scheduler and never run."
    )
    return payload, errors


@tool
def schedule_validate(
    agent_id: str,
    schedules_json: str = "",
    caller: Optional[str] = None,
) -> str:
    """
    Validates cron schedule entries for an agent's agent.json exactly as the
    scheduler will at load time. Call it before saving any schedule change.

    Checks: valid cron, a known `kind` (prompt|script), no unknown keys, a
    `precondition` from the library on prompt schedules (`always` needs a
    `reason`), `session_policy` (stateless|persistent) on prompt schedules only,
    and for script schedules that each script exists in scripts/, defines
    has_work(ctx) and keeps side effects under `if __name__ == "__main__":`.

    Args:
        agent_id: The agent whose schedules these are (e.g. 'wiki-gardener').
        schedules_json: JSON for one entry, a list of entries, or a full
            agent.json. Omit to validate the agent's saved agent.json.
        caller: The ID of the triggering agent.
    """
    payload, errors = validate_schedules(agent_id, schedules_json)
    return format_tool_response(TOOL_NAME, payload=payload, errors=errors)
