import os
from typing import Dict, Any, List
from langchain_core.messages import AIMessage
from graphs.coding.schemas import CodingState
from graphs.coding.utils import git_ops
from graphs.coding.utils.dag import update_task_in_queue, save_manifest, resolve_manifest_path

async def git_handoff_node(state: CodingState) -> Dict[str, Any]:
    """
    Merge & Teardown Node (v2):
    1. Executes automated squash-merge of GitHub PR into origin/main.
    2. Captures resulting commit URL on main.
    3. Tears down isolated worktree (workspaces/runs/{run_id}/).
    4. Updates build_request.json with status 'completed' and 'commit_url'.
    5. Dispatches task completion notification.
    """
    workspace_path = state.get("workspace_path", "")
    branch_name = state.get("branch_name", "")
    current_task = state.get("current_task") or {}
    task_id = current_task.get("task_id")
    if not task_id:
        return {
            "error_message": "Merge & Teardown error: Missing required 'task_id' in current_task state."
        }
    project_name = current_task.get("project_name") or state.get("project_name") or "coding_project"
    feature_name = current_task.get("feature_name") or task_id
    run_id = state.get("run_id") or "run_default"
    spec_path = state.get("master_spec_path") or current_task.get("spec_path", "")
    project_path = state.get("project_path", "")
    pr_url = state.get("pr_url") or current_task.get("pr_url", "")
    pr_number = state.get("pr_number")

    # 1. Automated Squash-Merge
    target_pr = pr_url or (str(pr_number) if pr_number else "")
    commit_url = ""
    target_dir = workspace_path or project_path or "."
    if target_pr:
        merge_ok, merge_commit, merge_log = await git_ops.merge_pull_request(
            workspace_path=target_dir,
            pr_url_or_number=target_pr,
            squash=True,
            delete_branch=True
        )
        if merge_ok and merge_commit:
            commit_url = merge_commit
        else:
            commit_url = f"{pr_url}#merged"
    elif pr_url:
        commit_url = f"{pr_url}#merged"

    # 2. Teardown worktree
    if project_path and workspace_path:
        await git_ops.teardown_worktree(project_path, workspace_path)

    # 3. Update queue and manifest
    queue = state.get("queue") or []
    updated_queue = update_task_in_queue(
        queue=queue,
        task_id=task_id,
        status="completed",
        branch_name=branch_name,
        pr_url=pr_url,
        commit_url=commit_url
    )

    manifest_path = resolve_manifest_path(state.get("build_request_path"), project_path)
    save_manifest(manifest_path, {
        "version": "2.0",
        "project_name": project_name,
        "max_concurrency": state.get("max_concurrency", 1),
        "queue": updated_queue
    })

    completed = list(state.get("completed_tasks", []))
    if task_id not in completed:
        completed.append(task_id)

    # 4. Construct completion message
    delivery_msg = (
        f"### 🚀 Task Completed & Merged\n"
        f"- **Task ID**: `{task_id}`\n"
        f"- **PR**: 🔗 [{pr_url}]({pr_url})\n"
        f"- **Merged Commit on Main**: 🔗 [{commit_url}]({commit_url})\n"
        f"- **Status**: Completed & Verified ✅"
    )

    messages = list(state.get("messages", []))
    messages.append(AIMessage(content=delivery_msg))

    return {
        "commit_url": commit_url,
        "pr_url": pr_url,
        "queue": updated_queue,
        "completed_tasks": completed,
        "current_task": None,
        "messages": messages,
        "error_message": ""
    }
