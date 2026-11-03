"""git_handoff — Node for git handoff processing and queue state update on disk."""
from typing import Any, Dict, List
from graphs.coding.schemas import CodingState
from graphs.coding.utils.dag import load_manifest, save_manifest, resolve_manifest_path


def update_task_in_queue(queue: List[Dict[str, Any]], task_id: str, updates: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Updates task attributes in queue in-place."""
    for task in queue:
        if task.get("task_id") == task_id:
            task.update(updates)
            break
    return queue


async def git_handoff_node(state: CodingState) -> Dict[str, Any]:
    """Node for git handoff processing, re-reading manifest from disk to avoid state clobbering."""
    manifest_path = resolve_manifest_path(state.get("build_request_path"))
    current_task = state.get("current_task") or {}
    task_id = current_task.get("task_id")

    if not task_id or not manifest_path:
        return {"error_message": "git_handoff: missing task_id or manifest_path", "route": "done"}

    # Re-read queue from disk as the source of truth right before mutating and saving
    manifest = load_manifest(manifest_path)
    queue = manifest.get("queue") or []

    status_update = {
        "status": current_task.get("status", "completed"),
        "stage": current_task.get("stage", "pushed")
    }
    update_task_in_queue(queue, task_id, status_update)
    manifest["queue"] = queue

    save_manifest(manifest_path, manifest)

    return {
        "queue": queue,
        "current_task": current_task,
        "route": "done",
        "error_message": ""
    }
