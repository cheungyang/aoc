import os
import uuid
from typing import Dict, Any, List
from graphs.coding.schemas import CodingState, TaskEnvelope
from graphs.coding.utils.dag import (
    resolve_manifest_path,
    load_manifest,
    save_manifest,
    get_runnable_tasks,
    update_task_in_queue
)

async def dag_scheduler_node(state: CodingState) -> Dict[str, Any]:
    """
    DAG Scheduler & Dispatcher:
    1. Loads or refreshes build_request queue.
    2. Resolves topological dependencies across tasks.
    3. Resolves dependent base_branch (inherits prerequisite branch if dependent, else origin/main).
    4. Dispatches next eligible pending task for execution.
    """
    project_path = state.get("project_path")
    if not project_path:
        return {
            "error_message": "DAG scheduler error: 'project_path' is missing from graph state. Explicit project_path is required."
        }

    manifest_path = resolve_manifest_path(state.get("build_request_path"), project_path)
    
    # 1. Obtain current queue
    queue = state.get("queue")
    project_name = state.get("project_name")
    
    if not queue:
        manifest_data = load_manifest(manifest_path)
        queue = manifest_data.get("queue", [])
        project_name = project_name or manifest_data.get("project_name", "coding_project")

    completed_tasks = [t["task_id"] for t in queue if t.get("status") == "completed"]
    failed_tasks = [t["task_id"] for t in queue if t.get("status") in ["failed", "blocked"]]

    # 2. Check if we have runnable tasks
    runnable = get_runnable_tasks(queue, max_count=1)

    if not runnable:
        # All tasks finished or blocked
        has_pending = any(t.get("status") == "pending" for t in queue)
        if has_pending:
            error_msg = "DAG scheduling stalled: remaining pending tasks have unresolved or cyclic dependencies."
        else:
            error_msg = ""

        return {
            "build_request_path": manifest_path,
            "project_name": project_name,
            "queue": queue,
            "completed_tasks": completed_tasks,
            "failed_tasks": failed_tasks,
            "current_task": None,
            "pr_url": state.get("pr_url", ""),
            "error_message": error_msg
        }

    # 3. Select next runnable task
    selected_task = dict(runnable[0])
    task_id = selected_task["task_id"]
    
    # Generate run_id if not present
    run_id = selected_task.get("run_id") or f"run_{uuid.uuid4().hex[:4].upper()}"
    selected_task["run_id"] = run_id
    selected_task["status"] = "in_progress"

    # 4. Resolve base_branch: Dependent Branch Inheritance (Scenario 2)
    deps = selected_task.get("dependencies") or []
    base_branch = "origin/main"
    if deps:
        # Find the last prerequisite task in the queue
        for prereq_id in reversed(deps):
            for t in queue:
                if t.get("task_id") == prereq_id and t.get("branch_name"):
                    base_branch = t["branch_name"]
                    break
            if base_branch != "origin/main":
                break
    else:
        base_branch = state.get("base_branch") or state.get("base_ref") or "origin/main"
    
    # Update queue
    updated_queue = update_task_in_queue(queue, task_id, status="in_progress", run_id=run_id)
    
    # Sync manifest to disk
    save_manifest(manifest_path, {
        "version": "1.0",
        "project_name": project_name,
        "max_concurrency": state.get("max_concurrency", 1),
        "queue": updated_queue
    })

    return {
        "build_request_path": manifest_path,
        "project_name": selected_task.get("project_name") or project_name,
        "queue": updated_queue,
        "completed_tasks": completed_tasks,
        "failed_tasks": failed_tasks,
        "current_task": selected_task,
        "run_id": run_id,
        "base_branch": base_branch,
        "master_spec_path": selected_task.get("spec_path", ""),
        "attempt_count": 0,
        "test_run_passed": False,
        "critic_passed": False,
        "test_stdout": "",
        "test_stderr": "",
        "critic_feedback": "",
        "latest_human_feedback": "",
        "hitl_decision": "",
        "error_message": ""
    }
