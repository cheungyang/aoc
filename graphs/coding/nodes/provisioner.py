import os
from typing import Dict, Any
from graphs.coding.schemas import CodingState
from graphs.coding.utils.dag import resolve_path, resolve_manifest_path
from graphs.coding.utils.repo import get_repo_descriptor, resolve_push_identity
from core.util import git_ops

async def provisioner_node(state: CodingState) -> Dict[str, Any]:
    """
    Worktree Provisioner Node:
    Deterministically provisions a sandboxed Git worktree directory for the current run
    at workspaces/runs/{run_id}/ with dedicated branch feat/{project}/{feature}_{run_id}.

    Also the preflight point: when the manifest names a machine user, its credential and
    push permission are checked here, before the worker spends any tokens.
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

    # Preflight: a machine user that cannot push must stop the run here. Discovering it
    # after the worker has written code means paying for an LLM run that cannot publish,
    # and falling back to the human's credentials would open a PR they cannot approve.
    repo_descriptor = get_repo_descriptor(state)
    push_identity, identity_error = resolve_push_identity(repo_descriptor)
    if identity_error:
        return {
            "workspace_path": workspace_path,
            "project_path": project_path,
            "build_request_path": build_request_path,
            "spec_path": resolved_spec_path,
            "branch_name": branch_name,
            "error_message": f"Preflight failed: {identity_error}"
        }

    if push_identity:
        target_repo = (
            repo_descriptor.get("slug")
            or state.get("target_repo")
            or current_task.get("target_repo")
        )
        ok, message = await git_ops.preflight_push_access(
            target_repo=target_repo,
            cwd=".",
            push_identity=push_identity
        )
        if not ok:
            return {
                "workspace_path": workspace_path,
                "project_path": project_path,
                "build_request_path": build_request_path,
                "spec_path": resolved_spec_path,
                "branch_name": branch_name,
                "error_message": message
            }

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
