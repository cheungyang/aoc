import os
from typing import Dict, Any
from graphs.coding.schemas import CodingState
from graphs.coding.utils.dag import resolve_path, resolve_manifest_path
from graphs.coding.utils import git_ops

async def provisioner_node(state: CodingState) -> Dict[str, Any]:
    """
    Worktree Provisioner Node:
    Deterministically provisions a sandboxed Git worktree directory for the current run
    at workspaces/runs/{run_id}/ with dedicated branch feat/{project}/{feature}_{run_id}.
    """
    current_task = state.get("current_task") or {}
    run_id = state.get("run_id") or current_task.get("run_id") or "run_default"
    project_name = current_task.get("project_name") or state.get("project_name") or "coding_project"
    feature_name = current_task.get("feature_name") or current_task.get("task_id") or "feat"
    
    # Sanitize feature and project names for git branch
    clean_project = str(project_name).replace(" ", "_").replace("/", "_")
    clean_feature = str(feature_name).replace(" ", "_").replace("/", "_")
    branch_name = f"feat/{clean_project}/{clean_feature}_{run_id}"

    # Pre-define resolved absolute paths for the entire graph lifecycle
    workspace_path = os.path.abspath(os.path.join("workspaces", "runs", run_id))
    project_path = resolve_path(state.get("project_path", "")) if state.get("project_path") else ""
    build_request_path = resolve_manifest_path(state.get("build_request_path"))
    
    raw_spec = current_task.get("spec_path") or state.get("spec_path", "")
    if raw_spec:
        try:
            resolved_spec_path = resolve_path(raw_spec, must_exist=True)
        except Exception as e:
            return {
                "workspace_path": workspace_path,
                "project_path": project_path,
                "build_request_path": build_request_path,
                "spec_path": "",
                "branch_name": branch_name,
                "error_message": f"Spec path error: {e}"
            }
    else:
        resolved_spec_path = ""

    # Base branch (inherits from prerequisite task or origin/main)
    base_ref = state.get("base_branch") or state.get("base_ref")

    # Provision worktree from current execution root
    success, msg = await git_ops.provision_worktree(
        repo_path=".",
        workspace_path=workspace_path,
        branch_name=branch_name,
        base_ref=base_ref
    )

    if not success:
        return {
            "workspace_path": workspace_path,
            "project_path": project_path,
            "build_request_path": build_request_path,
            "spec_path": resolved_spec_path,
            "branch_name": branch_name,
            "error_message": f"Worktree provisioning failed: {msg}"
        }

    return {
        "workspace_path": workspace_path,
        "project_path": project_path,
        "build_request_path": build_request_path,
        "spec_path": resolved_spec_path,
        "branch_name": branch_name,
        "error_message": ""
    }
