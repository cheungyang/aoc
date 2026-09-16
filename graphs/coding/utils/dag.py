import os
import glob
import json
import re
from typing import List, Dict, Any, Optional, Tuple
from graphs.coding.schemas import TaskEnvelope
from graphs.coding.utils import manifest as manifest_store

# One manifest per project, under its own spec folder. A single shared queue
# made every project's tasks each other's problem: one halted task held the
# concurrency slot, and `max_concurrency`, `repo` and `setup_command` — all
# project-scoped settings — had to be identical for everything in the queue.
SOFTWARE_ROOT = "pkm/wiki/software"
MANIFEST_FILENAME = "build_request.json"

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


def project_slug(project_name: str) -> str:
    """The folder a project's specs and manifest live in.

    Accepts what people actually type — `French Learning Cards`,
    `french_learning_cards` — and lands on one folder name either way.
    """
    slug = re.sub(r"[\s_]+", "-", str(project_name).strip().lower())
    return re.sub(r"[^a-z0-9.-]", "", slug).strip("-")


def manifest_path_for_project(project_name: str, root: str = SOFTWARE_ROOT) -> str:
    """Where a named project's manifest lives, whether or not it exists yet."""
    slug = project_slug(project_name)
    if not slug:
        return ""
    return os.path.abspath(os.path.join(root, slug, MANIFEST_FILENAME))


def discover_manifests(root: str = SOFTWARE_ROOT) -> List[str]:
    """Every project manifest, sorted.

    Only the operator entry points use this: a graph run is always told which
    one project it is working on. Discovery here is so the scheduled tick can
    visit each project in turn without a list to keep in sync.
    """
    pattern = os.path.join(os.path.abspath(root), "*", MANIFEST_FILENAME)
    return sorted(glob.glob(pattern))


def resolve_manifest_path(path: Optional[str] = None, *args, **kwargs) -> str:
    """Resolves the manifest path a run was given.

    There is deliberately no default. A global fallback is how a run that was
    never told which project it was for still found *a* queue — the wrong one —
    and started working through somebody else's tasks.
    """
    if not path:
        raise ValueError(
            "No build_request.json was given. The coding graph works on one "
            "project at a time: pass `build_request_path` (or `project_name`, "
            f"resolved to {SOFTWARE_ROOT}/<project>/{MANIFEST_FILENAME})."
        )
    return resolve_path(path)



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
