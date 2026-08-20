import os
from typing import Dict, Any, List
from langchain_core.messages import AIMessage
from graphs.coding.schemas import CodingState
from graphs.coding.utils import git_ops
from graphs.coding.utils.dag import update_task_in_queue, save_manifest, resolve_manifest_path

async def git_handoff_node(state: CodingState) -> Dict[str, Any]:
    """
    Git Handoff & Teardown Node:
    1. Stages and commits worktree changes.
    2. Pushes branch to remote origin.
    3. Creates GitHub PR via gh CLI.
    4. Tears down temporary worktree.
    5. Updates build_request.json with task completion and PR URL.
    """
    workspace_path = state.get("workspace_path", "")
    branch_name = state.get("branch_name", "")
    current_task = state.get("current_task") or {}
    task_id = current_task.get("task_id")
    if not task_id:
        return {
            "error_message": "Git handoff error: Missing required 'task_id' in current_task state."
        }
    project_name = current_task.get("project_name") or state.get("project_name") or "coding_project"
    feature_name = current_task.get("feature_name") or task_id
    run_id = state.get("run_id") or "run_default"
    spec_path = state.get("master_spec_path") or current_task.get("spec_path", "")
    project_path = state.get("project_path", "")

    # 1. Commit and Push
    commit_msg = f"feat({project_name}): implement {feature_name} ({run_id})"
    commit_ok, commit_log = await git_ops.commit_and_push(workspace_path, branch_name, commit_msg)
    if not commit_ok:
        return {
            "error_message": f"Git commit/push failed: {commit_log}"
        }

    # 2. Create Pull Request
    pr_title = f"feat: {feature_name}"
    pr_body = f"Automated PR from EGM Coding Graph for task {task_id}\n\n- **Project**: `{project_name}`\n- **Run ID**: `{run_id}`\n- **Spec**: `{spec_path}`"
    
    pr_ok, pr_res = await git_ops.create_pull_request(
        workspace_path=workspace_path,
        branch_name=branch_name,
        title=pr_title,
        body=pr_body
    )
    pr_url = pr_res if pr_ok else ""

    # 3. Teardown worktree
    if project_path:
        await git_ops.teardown_worktree(project_path, workspace_path)

    # 4. Update queue and manifest
    queue = state.get("queue") or []
    updated_queue = update_task_in_queue(
        queue=queue,
        task_id=task_id,
        status="completed",
        branch_name=branch_name,
        pr_url=pr_url
    )

    manifest_path = resolve_manifest_path(state.get("build_request_path"), project_path)
    save_manifest(manifest_path, {
        "version": "1.0",
        "project_name": project_name,
        "max_concurrency": state.get("max_concurrency", 1),
        "queue": updated_queue
    })

    completed = list(state.get("completed_tasks", []))
    if task_id not in completed:
        completed.append(task_id)

    if pr_ok and pr_url:
        delivery_msg = f"✅ Task `{task_id}` committed, pushed, and Pull Request created!\n- **Branch**: `{branch_name}`\n- **PR**: {pr_url}"
    else:
        delivery_msg = f"⚠️ Task `{task_id}` committed and pushed to `{branch_name}`, but Pull Request creation failed ({pr_res})."

    messages = list(state.get("messages", []))
    messages.append(AIMessage(content=delivery_msg))

    return {
        "pr_url": pr_url,
        "queue": updated_queue,
        "completed_tasks": completed,
        "current_task": None,
        "messages": messages,
        "error_message": ""
    }
