"""New problems in Home Assistant since the last audit, for the scan that gates it.

The daily audit ran a model turn every morning to re-read the same recurring
errors and the same unavailable entities (149 on the reference home, offline
since the last restart) and report them again. This module answers in code
whether there is a new problem, and writes the findings the audit reports from:

  * New errors. Home Assistant's system log -- the deduplicated view under
    Settings > System > Logs -- folds repeats of one error (same logger, code
    location and root cause) into one entry, with a count and the times it was
    first and last seen. An ERROR or CRITICAL entry first seen after the last
    audit is new. A recurring error that was already reported stays quiet, and
    a message whose numbers change every time is still one entry. Warnings
    never wake the audit.
  * Entities that turned unavailable or unknown after the last audit. Their
    `last_changed` says when, but a Home Assistant restart resets it for every
    entity, so the recorder's history is asked what those entities were at the
    last audit: one that was already unavailable then is not news. Unknown
    counts only for an entity that had a real state then -- a new button or
    scene starts as unknown and stays that way, which is no problem.

Recoveries and problems that were already there don't wake the audit; the
findings give their counts only. Nothing is stored on our side, so the check
has no side effects. "Since" is the start of the last *successful* run, so a
skipped or failed run leaves the same news for the next one. The price is that
the inputs follow Home Assistant's memory: the system log restarts empty with
HA, so after a restart every error logged again counts as new once; and the
recorder keeps `purge_keep_days` of history (10 by default), so after a longer
quiet spell an entity that changed in the meantime and is unavailable now
counts as newly unavailable.
"""
import itertools
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Dict, Iterable, List, Tuple

from agents.home_steward.lib.homeassistant.ws import HomeAssistantWebSocket

ERROR_LEVELS = frozenset({"ERROR", "CRITICAL"})
UNAVAILABLE = "unavailable"
UNKNOWN = "unknown"
PROBLEM_STATES = frozenset({UNAVAILABLE, UNKNOWN})

MAX_LINES = 20          # per findings section; the rest are counted
MESSAGE_CHARS = 200     # one error's message in the findings

NO_AREA = "No area"


@dataclass(frozen=True)
class AuditNews:
    summary: str        # one line, for the scheduler's log
    findings: str       # what the audit reports from; "" when nothing is new


# -- Deciding --------------------------------------------------------------------


def _is_error(entry: Dict[str, Any]) -> bool:
    return str(entry.get("level", "")).upper() in ERROR_LEVELS


def new_errors(entries: Iterable[Dict[str, Any]], since: float) -> List[Dict[str, Any]]:
    """ERROR and CRITICAL system-log entries first seen after `since`, newest first."""
    picked = [e for e in entries or [] if _is_error(e) and (e.get("first_occurred") or 0) > since]
    return sorted(picked, key=lambda e: e.get("first_occurred") or 0, reverse=True)


def recurring_errors(entries: Iterable[Dict[str, Any]], since: float) -> int:
    """How many older ERROR and CRITICAL entries have occurred again since `since`."""
    return sum(
        1 for e in entries or []
        if _is_error(e) and (e.get("first_occurred") or 0) <= since < (e.get("timestamp") or 0)
    )


def _changed_after(state: Dict[str, Any], since: float) -> bool:
    changed = state.get("last_changed")
    return bool(changed) and datetime.fromisoformat(changed).timestamp() > since


def problem_candidates(states: Iterable[Dict[str, Any]], since: float) -> List[str]:
    """Entities unavailable or unknown now whose state changed after `since`:
    the only ones that can be new problems, so the only ones looked up."""
    return sorted(
        s["entity_id"] for s in states
        if s.get("entity_id") and s.get("state") in PROBLEM_STATES and _changed_after(s, since)
    )


def new_problem_entities(
    states: Iterable[Dict[str, Any]],
    states_then: Dict[str, str],
    since: float,
) -> List[str]:
    """Entities that turned unavailable or unknown after `since`.

    `states_then` holds the state at `since` of the candidates
    (`problem_candidates`). Unavailable counts unless the entity was already
    unavailable or unknown then; one with no record then (a device added since
    that never came up) counts too. Unknown counts only when the entity had a
    real state then.
    """
    new = []
    for s in states:
        entity_id, state = s.get("entity_id"), s.get("state")
        if state not in PROBLEM_STATES or not _changed_after(s, since):
            continue
        then = states_then.get(entity_id)
        if then in PROBLEM_STATES or (state == UNKNOWN and then is None):
            continue
        new.append(entity_id)
    return sorted(new)


# -- Reading -----------------------------------------------------------------------


def states_at(ws: HomeAssistantWebSocket, when: float, entity_ids: List[str]) -> Dict[str, str]:
    """Each entity's state at `when`, from the recorder; ones it has no record of are left out.

    Asks for a one-second window starting at `when`: the first row per entity is
    its state at that instant. Over the WebSocket because the REST history API
    takes the entity ids in the URL, and after a restart that is all of them.
    """
    if not entity_ids:
        return {}
    start = datetime.fromtimestamp(when, timezone.utc)
    history = ws.command(
        "history/history_during_period",
        start_time=start.isoformat(),
        end_time=(start + timedelta(seconds=1)).isoformat(),
        entity_ids=entity_ids,
        include_start_time_state=True,
        significant_changes_only=False,
        minimal_response=True,
        no_attributes=True,
    )
    return {entity_id: rows[0].get("s") for entity_id, rows in (history or {}).items() if rows}


def check(since: float, tz: tzinfo) -> AuditNews:
    """The problems Home Assistant has that are new since `since` (epoch seconds).

    One WebSocket conversation: the system log and the states, the recorder only
    for entities that changed, and the registries only when there is news to
    describe. Connection errors propagate.
    """
    with HomeAssistantWebSocket() as ws:
        log = ws.command("system_log/list") or []
        states = ws.command("get_states") or []
        errors = new_errors(log, since)
        then = states_at(ws, since, problem_candidates(states, since))
        entities = new_problem_entities(states, then, since)
        still_unavailable = sum(
            1 for s in states if s.get("state") == UNAVAILABLE and s.get("entity_id") not in entities
        )
        recurring = recurring_errors(log, since)

        summary = (
            f"{len(errors)} new error(s), {len(entities)} newly unavailable or unknown; "
            f"ongoing: {still_unavailable} unavailable, {recurring} recurring error(s)"
        )
        if not (errors or entities):
            return AuditNews(summary, "")
        places = Places(ws.entities(), ws.devices(), ws.areas(), states)

    return AuditNews(summary, findings(errors, entities, still_unavailable, recurring, places, since, tz))


# -- Describing --------------------------------------------------------------------


class Places:
    """Where an entity is and what it's called, for grouping the findings by device."""

    def __init__(self, entities, devices, areas, states):
        self._entities = {e.get("entity_id"): e for e in entities or []}
        self._devices = {d.get("id"): d for d in devices or []}
        self._areas = {a.get("area_id"): a.get("name") for a in areas or []}
        self._names = {s.get("entity_id"): (s.get("attributes") or {}).get("friendly_name") for s in states}

    def name(self, entity_id: str) -> str:
        entry = self._entities.get(entity_id) or {}
        return self._names.get(entity_id) or entry.get("name") or entry.get("original_name") or entity_id

    def group(self, entity_id: str) -> Tuple[str, str]:
        """(area, what broke): the device, else the integration, else the entity itself.

        A device going offline takes all its entities with it, and so does a cloud
        integration for its entities that have no device; one line each.
        """
        entry = self._entities.get(entity_id) or {}
        device = self._devices.get(entry.get("device_id")) or {}
        area = self._areas.get(entry.get("area_id") or device.get("area_id")) or ""
        if device:
            return area, device.get("name_by_user") or device.get("name") or entity_id
        return area, entry.get("platform") or entity_id


def _when(epoch, tz: tzinfo) -> str:
    return datetime.fromtimestamp(epoch, tz).strftime("%a %H:%M") if epoch else "?"


def _error_line(entry: Dict[str, Any], tz: tzinfo) -> str:
    messages = entry.get("message") or [""]
    message = re.sub(r"\s+", " ", str(messages[0] if isinstance(messages, list) else messages)).strip()
    if len(message) > MESSAGE_CHARS:
        message = message[:MESSAGE_CHARS - 3] + "..."
    count = entry.get("count") or 1
    seen = f"first {_when(entry.get('first_occurred'), tz)}"
    if count > 1:
        seen += f", last {_when(entry.get('timestamp'), tz)}, x{count}"
    return f"- {entry.get('level', 'ERROR')} {entry.get('name', '?')} ({seen}): {message}"


def _entities(count: int) -> str:
    return f"{count} entity" if count == 1 else f"{count} entities"


def _entity_lines(entity_ids: List[str], places: Places) -> List[str]:
    """One line per device (or per entity without one), by area, no area last."""
    keyed = sorted(
        ((places.group(e), e) for e in entity_ids),
        key=lambda item: (not item[0][0], item[0][0].lower(), item[0][1].lower(), item[1]),
    )
    lines = []
    for (area, label), members in itertools.groupby(keyed, key=lambda item: item[0]):
        ids = [entity_id for _, entity_id in members]
        what = f"{label} ({_entities(len(ids))})" if len(ids) > 1 else f"{places.name(ids[0])} ({ids[0]})"
        lines.append(f"- {area or NO_AREA}: {what}")
    return lines


def _capped(lines: List[str]) -> List[str]:
    if len(lines) <= MAX_LINES:
        return lines
    return lines[:MAX_LINES] + [f"- +{len(lines) - MAX_LINES} more"]


def findings(
    errors: List[Dict[str, Any]],
    entities: List[str],
    still_unavailable: int,
    recurring: int,
    places: Places,
    since: float,
    tz: tzinfo,
) -> str:
    """The findings text the audit reports from: the new problems, then counts of the old ones."""
    parts = [f"New problems since {_when(since, tz)}."]
    if errors:
        parts.append(f"New errors ({len(errors)}), newest first:")
        parts.extend(_capped([_error_line(e, tz) for e in errors]))
    if entities:
        parts.append(f"Newly unavailable or unknown ({_entities(len(entities))}):")
        parts.extend(_capped(_entity_lines(entities, places)))
    parts.append(
        f"Still ongoing from before: {_entities(still_unavailable)} unavailable, "
        f"{recurring} older error(s) still recurring."
    )
    return "\n".join(parts)
