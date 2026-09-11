"""Durable persistence for the coding manifest.

The manifest is the system of record: the graph runs without a checkpointer, so
"retry" means "run another tick", and every fact a tick needs to resume must be
on disk. Three properties make that safe:

1. **Atomic writes.** Write to a temp file in the same directory, then
   `os.replace`. A crash mid-write leaves the previous manifest intact rather
   than a truncated one.
2. **Locked read-modify-write.** Nodes used to rewrite the whole document from
   memory, so two writers silently lost each other's updates and keys nobody
   was managing (the repo descriptor, another task's PR url) disappeared.
   `locked_manifest()` and `persist_task()` re-read under an exclusive `flock`
   and write back the merged document.
3. **Leases.** A task claimed by a process that then dies is reclaimed on the
   next tick, which is what turns "stuck in_progress forever" into automatic
   crash recovery.

Concurrency is 1 today. These invariants are cheap now and are what allow N>1
later without revisiting every writer.
"""
import fcntl
import json
import os
import tempfile
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

from graphs.coding.schemas import STAGE_ORDER, TaskEnvelope

MANIFEST_VERSION = "3.0"

# How long a claim stays valid without a heartbeat. Long enough for a slow LLM
# turn, short enough that a crashed run is picked up on a following tick.
DEFAULT_LEASE_SECONDS = 30 * 60

# v2 wrote these; v3 reads them once and rewrites in the new vocabulary.
LEGACY_STATUS_MAP = {
    "pending": "queued",
    "in_progress": "active",
    "in_review": "awaiting_review",
    "completed": "done",
    "rejected": "failed",
}

# Best-effort stage for a task migrated mid-flight. Deliberately conservative:
# an `active` task resumes from `queued` and re-provisions (cheap, idempotent)
# rather than claiming work that may never have happened.
LEGACY_STAGE_MAP = {
    "queued": "queued",
    "active": "queued",
    "awaiting_review": "awaiting_review",
    "done": "done",
    "failed": "queued",
    "halted": "queued",
    "blocked": "queued",
}

DEFAULT_MANIFEST: Dict[str, Any] = {
    "version": MANIFEST_VERSION,
    "project_name": "coding_project",
    "max_concurrency": 1,
    "queue": [],
}


def _empty_manifest() -> Dict[str, Any]:
    manifest = dict(DEFAULT_MANIFEST)
    manifest["queue"] = []
    return manifest


def migrate_manifest(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Upgrades a v1/v2 manifest to v3 in memory. Idempotent, never destructive."""
    if not data:
        return _empty_manifest()

    migrated = dict(data)
    migrated.setdefault("project_name", DEFAULT_MANIFEST["project_name"])
    migrated.setdefault("max_concurrency", DEFAULT_MANIFEST["max_concurrency"])

    queue: List[Dict[str, Any]] = []
    for raw in migrated.get("queue") or []:
        task = dict(raw)
        status = task.get("status") or "queued"
        task["status"] = LEGACY_STATUS_MAP.get(status, status)
        task.setdefault("stage", LEGACY_STAGE_MAP.get(task["status"], "queued"))
        task.setdefault("attempts", {})
        task.setdefault("updated_at", time.time())
        queue.append(task)

    migrated["queue"] = queue
    migrated["version"] = MANIFEST_VERSION
    return migrated


def load_manifest(manifest_path: str, migrate: bool = True) -> Dict[str, Any]:
    """Reads the manifest, upgrading it to v3 in memory (the file is untouched)."""
    if not os.path.exists(manifest_path):
        return _empty_manifest()

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        # A corrupt manifest must not be silently replaced by an empty one: that
        # would look like "all tasks finished" and the queue would vanish.
        raise ValueError(f"Manifest at {manifest_path} is not readable JSON: {e}") from e

    return migrate_manifest(data) if migrate else data


def save_manifest(manifest_path: str, data: Dict[str, Any]) -> bool:
    """Writes the manifest atomically (temp file in the same dir, then replace)."""
    try:
        abs_path = os.path.abspath(manifest_path)
        directory = os.path.dirname(abs_path)
        os.makedirs(directory, exist_ok=True)

        fd, tmp_path = tempfile.mkstemp(prefix=".build_request.", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, abs_path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
        return True
    except Exception as e:
        print(f"Error saving manifest to {manifest_path}: {e}")
        return False


@contextmanager
def locked_manifest(manifest_path: str, migrate: bool = True):
    """Exclusive read-modify-write. Yields the manifest dict; mutations are saved.

    The lock lives in a sidecar `.lock` file rather than on the manifest itself,
    because the atomic write replaces the inode and would drop a lock held on it.
    """
    abs_path = os.path.abspath(manifest_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    lock_path = abs_path + ".lock"

    with open(lock_path, "a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            data = load_manifest(abs_path, migrate=migrate)
            yield data
            save_manifest(abs_path, data)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def find_task(manifest: Dict[str, Any], task_id: str) -> Optional[Dict[str, Any]]:
    """Returns the live task dict inside the manifest (not a copy)."""
    for task in manifest.get("queue") or []:
        if task.get("task_id") == task_id:
            return task
    return None


def persist_task(manifest_path: str, task_id: str, **fields: Any) -> Optional[TaskEnvelope]:
    """Updates one task under lock, leaving every other key untouched.

    This is the only supported way to write task state. Returns the updated task,
    or None if the manifest has no such task.
    """
    updated: Optional[Dict[str, Any]] = None

    with locked_manifest(manifest_path) as manifest:
        task = find_task(manifest, task_id)
        if task is None:
            return None
        for key, value in fields.items():
            task[key] = value
        task["updated_at"] = time.time()
        updated = dict(task)

    return updated


def bump_attempt(manifest_path: str, task_id: str, stage: str) -> int:
    """Increments the attempt counter for one stage and returns the new value."""
    count = 0

    with locked_manifest(manifest_path) as manifest:
        task = find_task(manifest, task_id)
        if task is None:
            return 0
        attempts = dict(task.get("attempts") or {})
        count = int(attempts.get(stage, 0)) + 1
        attempts[stage] = count
        task["attempts"] = attempts
        task["updated_at"] = time.time()

    return count


def lease_is_active(task: Dict[str, Any], now: Optional[float] = None) -> bool:
    """True when another run still holds a valid claim on this task."""
    expires = task.get("lease_expires_at")
    if not task.get("lease_owner") or not expires:
        return False
    return float(expires) > (now if now is not None else time.time())


def acquire_lease(
    manifest_path: str,
    task_id: str,
    owner: str,
    ttl_seconds: float = DEFAULT_LEASE_SECONDS,
    now: Optional[float] = None
) -> bool:
    """Claims a task. Atomic under the manifest lock, so two ticks cannot both win.

    Re-acquiring your own lease succeeds and extends it: that is the heartbeat.
    """
    moment = now if now is not None else time.time()
    acquired = False

    with locked_manifest(manifest_path) as manifest:
        task = find_task(manifest, task_id)
        if task is None:
            return False
        if lease_is_active(task, moment) and task.get("lease_owner") != owner:
            return False

        task["lease_owner"] = owner
        task["lease_expires_at"] = moment + ttl_seconds
        task["updated_at"] = moment
        acquired = True

    return acquired


def release_lease(manifest_path: str, task_id: str, owner: Optional[str] = None) -> bool:
    """Releases a claim. With `owner` set, refuses to release someone else's."""
    released = False

    with locked_manifest(manifest_path) as manifest:
        task = find_task(manifest, task_id)
        if task is None:
            return False
        if owner is not None and task.get("lease_owner") not in (None, owner):
            return False

        task["lease_owner"] = None
        task["lease_expires_at"] = None
        task["updated_at"] = time.time()
        released = True

    return released


def reclaim_expired_leases(manifest_path: str, now: Optional[float] = None) -> List[str]:
    """Frees tasks whose owner died. Returns the reclaimed task ids.

    An `active` task with an expired lease goes back to `queued` at its recorded
    stage, so the next tick resumes it rather than restarting it.
    """
    moment = now if now is not None else time.time()
    reclaimed: List[str] = []

    with locked_manifest(manifest_path) as manifest:
        for task in manifest.get("queue") or []:
            if not task.get("lease_owner"):
                continue
            if lease_is_active(task, moment):
                continue

            task["lease_owner"] = None
            task["lease_expires_at"] = None
            if task.get("status") == "active":
                task["status"] = "queued"
            task["updated_at"] = moment
            reclaimed.append(task.get("task_id"))

    return reclaimed


def stage_at_or_past(task: Dict[str, Any], stage: str) -> bool:
    """True when the task already reached `stage`. The guard every node opens with."""
    current = task.get("stage") or "queued"
    try:
        return STAGE_ORDER.index(current) >= STAGE_ORDER.index(stage)
    except ValueError:
        return False


def next_stage(stage: str) -> Optional[str]:
    """The stage that follows `stage`, or None at the end of the machine."""
    try:
        index = STAGE_ORDER.index(stage)
    except ValueError:
        return None
    if index + 1 >= len(STAGE_ORDER):
        return None
    return STAGE_ORDER[index + 1]
