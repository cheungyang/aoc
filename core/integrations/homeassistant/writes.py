"""Applying changes to Home Assistant, reversibly.

Every config write follows the same pipeline, and the order is the point:

    before-image -> persist -> apply -> verify -> (rollback on failure)

The before-image is captured and written to disk *before* anything is sent. If
this process dies mid-apply, the snapshot is already on disk and a human can put
the old version back by hand. Capturing it after the write, or holding it only in
memory, would lose exactly the case it exists for.

Rollback is a re-`POST` of the before-image, or a `DELETE` when the object did not
previously exist. That is why "did not exist" is recorded explicitly rather than
inferred from a null: a missing before-image and an object whose config happens to
be empty need different undo actions, and conflating them would either orphan a
new automation or delete one the agent never created.

Nothing here decides *whether* a write is allowed -- that is `guards.py`, which
runs first. By the time anything in this module is called, the action has already
been authorised and, where required, confirmed by a human.
"""
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.integrations.homeassistant.client import HomeAssistantClient, HomeAssistantError
from core.util.config import Config

# The three object kinds HA exposes through its config API, and the path that
# serves all of GET/POST/DELETE for each. `POST` with a fresh id creates and with
# an existing id replaces, so there is no separate create endpoint.
CONFIG_OBJECTS = {
    "automation": "/api/config/automation/config/{object_id}",
    "script": "/api/config/script/config/{object_id}",
    "scene": "/api/config/scene/config/{object_id}",
}

# action -> (kind, is_delete)
CONFIG_ACTIONS = {
    "upsert_automation": ("automation", False),
    "upsert_script": ("script", False),
    "upsert_scene": ("scene", False),
    "delete_automation": ("automation", True),
    "delete_script": ("script", True),
    "delete_scene": ("scene", True),
}

# Where before-images go. Under the agent's own PKM directory, which every agent
# already has write rights to -- so rollback needs no new grant and no access to
# Home Assistant's config directory.
SNAPSHOT_AGENT = "home-steward"
SNAPSHOT_SUBDIR = "ha_snapshots"


class WriteError(HomeAssistantError):
    """A write failed. The message is written for the agent, not for a log file."""


@dataclass
class WriteResult:
    """What actually happened, in enough detail for an agent to explain it.

    `rolled_back` and `rollback_failed` are separate because they are different
    news: the first means the house is as it was, the second means it is not and a
    human needs the snapshot path.
    """

    action: str
    object_id: str
    kind: str
    created: bool = False
    applied: bool = False
    verified: bool = False
    rolled_back: bool = False
    rollback_failed: bool = False
    snapshot_path: Optional[str] = None
    detail: str = ""
    warnings: list = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        out = {
            "action": self.action,
            "kind": self.kind,
            "object_id": self.object_id,
            "created": self.created,
            "applied": self.applied,
            "verified": self.verified,
            "snapshot": self.snapshot_path,
            "detail": self.detail,
        }
        if self.rolled_back:
            out["rolled_back"] = True
        if self.rollback_failed:
            out["rollback_failed"] = True
        if self.warnings:
            out["warnings"] = self.warnings
        return out


# -- Snapshots ---------------------------------------------------------------


def snapshot_dir() -> str:
    """`<pkm>/agents/home-steward/ha_snapshots`, created on demand."""
    path = os.path.join(Config().pkm_dir, "agents", SNAPSHOT_AGENT, SNAPSHOT_SUBDIR)
    os.makedirs(path, exist_ok=True)
    return path


def write_snapshot(record: Dict[str, Any]) -> str:
    """Persists one before-image and returns its path.

    Named `<utc-timestamp>-<kind>-<object_id>.json` so the directory sorts
    chronologically and a specific object's history greps out of it.

    The timestamp carries microseconds, which is not cosmetic. At second
    resolution two writes to the same object within the same second produce the
    same filename, and the second silently overwrites the first -- destroying the
    before-image for the very sequence most likely to need it (an apply that fails
    verification and is immediately rolled back). An acceptance run hit exactly
    that: five writes left four snapshots.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    safe_id = str(record.get("object_id", "unknown")).replace("/", "_")
    filename = f"{stamp}-{record.get('kind', 'object')}-{safe_id}.json"
    path = os.path.join(snapshot_dir(), filename)

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, default=str)
    return path


# -- Before-images -----------------------------------------------------------


def fetch_before_image(rest: HomeAssistantClient, kind: str, object_id: str):
    """Returns `(existed, config)` for a config object.

    A 404 means the object is new, which is a normal outcome rather than an
    error -- it is how a create is distinguished from a replace, and it decides
    whether rollback is a DELETE or a restore. Any other failure is real and
    propagates: writing without knowing the previous state would leave nothing to
    roll back to.
    """
    path = CONFIG_OBJECTS[kind].format(object_id=object_id)
    try:
        return True, rest.get(path)
    except HomeAssistantError as exc:
        if "404" in str(exc):
            return False, None
        raise


# -- The pipeline ------------------------------------------------------------


def _mint_object_id() -> str:
    """HA's own UI uses a millisecond timestamp as the automation/scene id."""
    return str(int(time.time() * 1000))


def apply_config_write(
    rest: HomeAssistantClient,
    action: str,
    payload: Dict[str, Any],
    agent_id: Optional[str] = None,
) -> WriteResult:
    """Creates, replaces or deletes one config object, reversibly.

    Raises WriteError for anything that prevents a safe attempt (unknown action,
    missing id). Once the write is attempted, failures are reported in the
    returned WriteResult rather than raised, because by then there is a snapshot
    path and a rollback outcome the agent needs to relay.
    """
    if action not in CONFIG_ACTIONS:
        raise WriteError(f"'{action}' is not a config write.")

    kind, is_delete = CONFIG_ACTIONS[action]
    config = payload.get("config")
    object_id = str(payload.get("id") or "").strip()

    if is_delete and not object_id:
        raise WriteError(f"{action} requires 'id' -- the object to delete.")

    if not is_delete:
        if not isinstance(config, dict) or not config:
            raise WriteError(f"{action} requires a non-empty 'config' object.")
        if not object_id:
            # Scripts are the exception: the id *is* the entity's object_id
            # (script.<id>), so it is user-visible and must be chosen
            # deliberately. Automations and scenes use an opaque handle that
            # nothing reads, so minting one is safe and saves a round trip.
            if kind == "script":
                raise WriteError(
                    "upsert_script requires 'id': it becomes the entity id "
                    "(script.<id>), so it must be chosen rather than generated."
                )
            object_id = _mint_object_id()

    path = CONFIG_OBJECTS[kind].format(object_id=object_id)
    result = WriteResult(action=action, object_id=object_id, kind=kind)

    # 1. Before-image, captured and persisted before anything is sent.
    existed, before = fetch_before_image(rest, kind, object_id)
    result.created = not existed

    if is_delete and not existed:
        raise WriteError(f"No {kind} with id '{object_id}' exists, so there is nothing to delete.")

    result.snapshot_path = write_snapshot({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_id": agent_id,
        "action": action,
        "kind": kind,
        "object_id": object_id,
        "existed": existed,
        "before": before,
        "after": None if is_delete else config,
    })

    # 2. Apply. HA validates the config in this same call and answers 400 with a
    #    reason when it is malformed, so an invalid automation is rejected without
    #    ever being stored -- no separate validate step is needed for that case.
    try:
        if is_delete:
            rest.delete(path)
        else:
            rest.post(path, json=config)
        result.applied = True
    except HomeAssistantError as exc:
        result.detail = (
            f"Home Assistant rejected the {action}: {exc} "
            f"Nothing was changed; the before-image is at {result.snapshot_path}."
        )
        return result

    # 3. Verify by reading back, rather than trusting the write's own 200.
    verified, detail = _verify(rest, kind, object_id, config, expect_absent=is_delete)
    result.verified = verified
    result.detail = detail

    if verified:
        return result

    # 4. Roll back to the recorded state.
    _rollback(rest, result, path, before, existed)
    return result


def _verify(rest, kind, object_id, expected_config, expect_absent):
    """Reads the object back and confirms it is what we asked for."""
    try:
        existed, current = fetch_before_image(rest, kind, object_id)
    except HomeAssistantError as exc:
        return False, f"Applied, but could not read the {kind} back to verify it: {exc}"

    if expect_absent:
        if existed:
            return False, f"The {kind} '{object_id}' still exists after the delete."
        return True, f"Deleted {kind} '{object_id}'."

    if not existed:
        return False, f"The {kind} '{object_id}' is absent after the write."

    # HA normalises what it stores -- it adds an `id`, and may reorder or expand
    # shorthand keys -- so an exact match would fail on a correct write. Compare
    # only the keys we sent, which is what "did my change land?" actually means.
    mismatched = [
        key for key, value in (expected_config or {}).items()
        if key != "id" and isinstance(current, dict) and current.get(key) != value
    ]
    if mismatched:
        return False, (
            f"The stored {kind} does not match what was sent; "
            f"these keys differ: {', '.join(sorted(mismatched))}."
        )

    return True, f"Wrote {kind} '{object_id}' and verified it read back correctly."


def _rollback(rest, result: WriteResult, path: str, before, existed: bool) -> None:
    """Restores the recorded state, recording honestly whether it worked."""
    try:
        if existed:
            rest.post(path, json=before)
        else:
            rest.delete(path)
        result.rolled_back = True
        result.detail += (
            " Rolled back to the previous state."
            if existed else
            " Rolled back by deleting the newly created object."
        )
    except HomeAssistantError as exc:
        result.rollback_failed = True
        result.detail += (
            f" ROLLBACK ALSO FAILED ({exc}). Home Assistant may be in a partially "
            f"changed state and needs a human. The previous version is saved at "
            f"{result.snapshot_path}."
        )


# -- Service calls -----------------------------------------------------------


def call_service(
    rest: HomeAssistantClient,
    domain: str,
    service: str,
    entity_id: Optional[str] = None,
    service_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Calls one service.

    Not retried: see `HomeAssistantClient.request`. A service call is not
    idempotent in general, and a silent second delivery is a second change to the
    house.

    There is no before-image here. A service call has no stored previous version
    to restore -- undoing `light.turn_on` means calling `light.turn_off`, which is
    a decision for the agent and the user, not a mechanical rollback.
    """
    body = dict(service_data or {})
    if entity_id:
        body["entity_id"] = entity_id

    changed = rest.post(f"/api/services/{domain}/{service}", json=body, retry=False)

    # HA answers with the states it changed, which is the most useful confirmation
    # available: an empty list means the call was accepted but matched nothing.
    affected = [s.get("entity_id") for s in changed] if isinstance(changed, list) else []
    return {
        "called": f"{domain}.{service}",
        "entity_id": entity_id,
        "changed_entities": affected,
        "note": (
            "Home Assistant accepted the call but reported no state change. "
            "The entity id may not exist, or the service may have had no effect."
            if not affected else ""
        ),
    }


def reload(rest: HomeAssistantClient, domain: str = "homeassistant") -> Dict[str, Any]:
    """Reloads a domain's YAML config without restarting Home Assistant."""
    service = "reload_all" if domain == "homeassistant" else "reload"
    rest.post(f"/api/services/{domain}/{service}", json={}, retry=False)
    return {"reloaded": f"{domain}.{service}"}
