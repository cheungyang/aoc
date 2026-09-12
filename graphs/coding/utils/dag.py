import os
import json
from typing import List, Dict, Any, Optional, Tuple
from graphs.coding.schemas import TaskEnvelope, TaskStatus
from graphs.coding.utils import manifest as manifest_store

DEFAULT_MANIFEST_PATH = "pkm/wiki/software/build_request.json"

# A task is finished under either vocabulary: v2 wrote "completed", v3 writes "done".
COMPLETED_STATUSES = {"completed", "done"}
# Likewise for "ready to be picked up".
RUNNABLE_STATUSES = {"pending", "queued"}

def resolve_path(path: Optional[str], default: Optional[str] = None, must_exist: bool = False) -> str:
    """
    Resolves an absolute path from system root ($cwd/$path).
    If must_exist is True and the file does not exist, raises FileNotFoundError.
    No fallbacks or heuristic guessing.
    """
    target = path or default
    if not target:
        if must_exist:
            raise ValueError("Path is missing or empty.")
        return ""
    
    abs_path = os.path.abspath(target) if os.path.isabs(target) else os.path.abspath(os.path.join(os.getcwd(), target))
    
    if must_exist and not os.path.exists(abs_path):
        raise FileNotFoundError(f"Path not found at '{abs_path}' (resolved from system root).")
    
    return abs_path


def resolve_manifest_path(path: Optional[str] = None, *args, **kwargs) -> str:
    """Resolves absolute path to global build_request.json from system root."""
    return resolve_path(path, default=DEFAULT_MANIFEST_PATH)


def load_manifest(manifest_path: str) -> Dict[str, Any]:
    """Loads the build request manifest as written, without migrating it.

    This is the read-only view used for inspection and scheduling queries, so it
    deliberately does not upgrade statuses on disk; `utils.manifest.load_manifest`
    is the migrating reader used by the tick.
    """
    try:
        return manifest_store.load_manifest(manifest_path, migrate=False)
    except Exception as e:
        print(f"Error loading manifest from {manifest_path}: {e}")
        return {
            "version": "2.0",
            "project_name": "unknown_project",
            "max_concurrency": 1,
            "queue": []
        }


def save_manifest(manifest_path: str, data: Dict[str, Any]) -> bool:
    """Saves the manifest atomically (temp file + os.replace)."""
    return manifest_store.save_manifest(manifest_path, data)


def get_completed_task_ids(queue: List[TaskEnvelope]) -> set[str]:
    """Returns set of task_ids that finished successfully (v2 'completed' or v3 'done')."""
    return {
        t["task_id"]
        for t in queue
        if t.get("status") in COMPLETED_STATUSES
    }


def get_runnable_tasks(queue: List[TaskEnvelope], max_count: int = 1) -> List[TaskEnvelope]:
    """
    Evaluates topological dependencies and returns up to max_count tasks that are
    ready to run: status 'queued'/'pending', all dependencies finished, and not
    currently leased by another run.

    Dependencies gate on completion rather than on the prerequisite's branch
    existing, so a dependent cannot start against a branch the merge deleted (F3).
    """
    completed_ids = get_completed_task_ids(queue)
    runnable = []

    for task in queue:
        status = task.get("status", "pending")
        if status not in RUNNABLE_STATUSES:
            continue

        # Another live tick already owns this one.
        if manifest_store.lease_is_active(task):
            continue

        deps = task.get("dependencies") or []
        # Check if all dependencies are completed
        if all(dep in completed_ids for dep in deps):
            runnable.append(task)
            if len(runnable) >= max_count:
                break

    return runnable


def update_task_in_queue(
    queue: List[TaskEnvelope],
    task_id: str,
    status: Optional[TaskStatus] = None,
    run_id: Optional[str] = None,
    branch_name: Optional[str] = None,
    pr_url: Optional[str] = None,
    commit_url: Optional[str] = None,
    error_message: Optional[str] = None
) -> List[TaskEnvelope]:
    """Updates a task in the queue list in place and returns updated queue."""
    updated = []
    for t in queue:
        if t.get("task_id") == task_id:
            new_task = dict(t)
            if status is not None:
                new_task["status"] = status
            if run_id is not None:
                new_task["run_id"] = run_id
            if branch_name is not None:
                new_task["branch_name"] = branch_name
            if pr_url is not None:
                new_task["pr_url"] = pr_url
            if commit_url is not None:
                new_task["commit_url"] = commit_url
            if error_message is not None:
                new_task["error_message"] = error_message
            updated.append(new_task)
        else:
            updated.append(t)
    return updated
