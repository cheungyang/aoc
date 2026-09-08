import os
from typing import Dict, Any, List
from langchain_core.messages import AIMessage
from graphs.coding.schemas import CodingState
from graphs.coding.utils.dag import (
    resolve_manifest_path,
    load_manifest,
    save_manifest,
    update_task_in_queue,
)
from graphs.coding.utils import git_ops

async def termination_node(state: CodingState) -> Dict[str, Any]:
    """
    Termination Node (EGM-FEAT-01):
    Persists failed or aborted task statuses ('failed' or 'rejected') to the
    build_request manifest, tears down isolated worktree, and clears current_task state.
    """
    workspace_path = state.get("workspace_path", "")
    current_task = state.get("current_task") or {}
    task_id = current_task.get("task_id")
    project_name = current_task.get("project_name") or state.get("project_name") or "coding_project"
    hitl_decision = state.get("hitl_decision", "")
    branch_name = state.get("branch_name", "")
    pr_url = state.get("pr_url") or current_task.get("pr_url", "")

    # Determine terminal status: 'rejected' if aborted in HITL gate, else 'failed'
    if hitl_decision == "abort":
        terminal_status = "rejected"
        reason = "Task explicitly aborted during human review."
    else:
        terminal_status = "failed"
        test_err = state.get("test_stderr", "")
        critic_err = state.get("critic_feedback", "")
        err_msg = state.get("error_message", "")
        reason = test_err or critic_err or err_msg or "Task exceeded maximum retry attempts."

    # Teardown worktree if exists
    if workspace_path and os.path.exists(workspace_path):
        try:
            await git_ops.teardown_worktree(".", workspace_path)
        except Exception as e:
            print(f"termination_node: worktree teardown error: {e}")

    queue = state.get("queue") or []
    manifest_path = state.get("build_request_path") or resolve_manifest_path()

    if not queue and os.path.exists(manifest_path):
        manifest_data = load_manifest(manifest_path)
        queue = manifest_data.get("queue", [])

    if task_id:
        queue = update_task_in_queue(
            queue=queue,
            task_id=task_id,
            status=terminal_status,
            branch_name=branch_name,
            pr_url=pr_url,
            error_message=reason
        )

        save_manifest(manifest_path, {
            "version": "2.0",
            "project_name": project_name,
            "max_concurrency": state.get("max_concurrency", 1),
            "queue": queue
        })

    failed_tasks = list(state.get("failed_tasks", []))
    if task_id and task_id not in failed_tasks:
        failed_tasks.append(task_id)

    # Construct termination notification message
    status_icon = "🛑" if terminal_status == "rejected" else "❌"
    term_msg = (
        f"### {status_icon} Task Terminated: `{task_id}`\n"
        f"- **Status**: `{terminal_status.upper()}`\n"
        f"- **Reason**: {reason}\n"
    )
    if pr_url:
        term_msg += f"- **GitHub PR**: 🔗 [{pr_url}]({pr_url})\n"

    messages = list(state.get("messages", []))
    messages.append(AIMessage(content=term_msg))

    return {
        "workspace_path": "",
        "queue": queue,
        "failed_tasks": failed_tasks,
        "current_task": None,
        "messages": messages,
        "error_message": f"Task {task_id} terminated with status '{terminal_status}': {reason}"
    }
