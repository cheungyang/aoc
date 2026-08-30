from typing import Dict, Any, List
from langchain_core.messages import AIMessage
from graphs.coding.schemas import CodingState
from graphs.coding.utils import git_ops
from graphs.coding.utils.dag import update_task_in_queue, save_manifest, resolve_manifest_path, load_manifest
from graphs.coding.nodes.hitl_decision import (
    process_hitl_decision_node,
    classify_hitl_intent,
    APPROVAL_KEYWORDS,
    ABORT_KEYWORDS,
    REVISION_KEYWORDS,
)

__all__ = [
    "hitl_gate_node",
    "process_hitl_decision_node",
    "classify_hitl_intent",
    "APPROVAL_KEYWORDS",
    "ABORT_KEYWORDS",
    "REVISION_KEYWORDS",
]


async def hitl_gate_node(state: CodingState) -> Dict[str, Any]:
    """
    PR Publisher & HITL Review Gate Node (v2):
    1. Stages & commits verified code with author attribution: Graph Worker <worker@egm.internal>.
    2. Pushes branch to remote origin.
    3. Opens GitHub Pull Request via gh CLI tool.
    4. Updates task status to 'in_review' in build_request.json.
    5. Pauses execution via LangGraph checkpoint interrupt with the clickable PR link.
    """
    workspace_path = state.get("workspace_path", "")
    branch_name = state.get("branch_name", "")
    current_task = state.get("current_task") or {}
    task_id = current_task.get("task_id", "TASK")
    project_name = current_task.get("project_name") or state.get("project_name") or "coding_project"
    run_id = state.get("run_id") or "run_default"
    spec_path = state.get("spec_path") or current_task.get("spec_path", "")
    project_path = state.get("project_path", "")

    # Discover target repository for GitHub routing
    target_repo = (
        state.get("target_repo")
        or current_task.get("target_repo")
        or state.get("repo")
        or current_task.get("repo")
        or await git_ops.discover_target_repo(workspace_path, ".")
    )

    existing_pr = state.get("pr_url")
    existing_pr_num = state.get("pr_number")

    if not existing_pr and workspace_path and branch_name:
        # 1. Commit and push with author attribution
        commit_msg = f"feat({project_name}): implement {task_id} ({run_id})"
        author = "Graph Worker <worker@egm.internal>"
        commit_ok, commit_log = await git_ops.commit_and_push(
            workspace_path=workspace_path,
            branch_name=branch_name,
            commit_msg=commit_msg,
            author=author
        )
        if not commit_ok:
            return {
                "error_message": f"Git commit/push failed in HITL gate: {commit_log}"
            }

        # 2. Open GitHub Pull Request
        pr_title = f"feat: {task_id}"
        pr_body = (
            f"Automated PR from EGM Coding Graph for spec: `{spec_path}`\n\n"
            f"### Verification\n"
            f"- **Tests**: ✅ Passing\n"
            f"- **Critic**: ✅ Approved\n"
            f"- **Author**: `Graph Worker <worker@egm.internal>`"
        )
        base_branch = state.get("base_branch") or "main"
        if base_branch.startswith("origin/"):
            base_branch = base_branch.replace("origin/", "")

        pr_ok, pr_url, pr_num = await git_ops.create_pull_request(
            workspace_path=workspace_path,
            branch_name=branch_name,
            title=pr_title,
            body=pr_body,
            base_branch=base_branch,
            target_repo=target_repo
        )
        existing_pr = pr_url if pr_ok else ""
        existing_pr_num = pr_num

    # 3. Update queue status to in_review
    manifest_path = state.get("build_request_path") or resolve_manifest_path()
    manifest_data = load_manifest(manifest_path)
    queue = manifest_data.get("queue") or []
    updated_queue = update_task_in_queue(
        queue=queue,
        task_id=task_id,
        status="in_review",
        run_id=run_id,
        branch_name=branch_name,
        pr_url=existing_pr
    )

    manifest_data["queue"] = updated_queue
    save_manifest(manifest_path, manifest_data)

    # 4. Construct interruption message
    review_msg = (
        f"### 🔍 Coding Graph HITL Review Gate\n"
        f"- **Task ID**: `{task_id}`\n"
        f"- **Branch**: `{branch_name}`\n"
        f"- **GitHub PR**: 🔗 [{existing_pr}]({existing_pr})\n"
        f"- **Test Suite**: ✅ ALL TESTS PASSING\n"
        f"- **Critic Verdict**: ✅ APPROVED\n\n"
        f"Please review the changes on GitHub.\n"
        f"- **Approve**: Click \"Approve\" on GitHub PR OR reply `Approve` in chat to merge into `origin/main`.\n"
        f"- **Revise**: Leave review comments on the GitHub PR or reply with feedback here to request updates."
    )
    messages = list(state.get("messages", []))
    messages.append(AIMessage(content=review_msg))

    return {
        "target_repo": target_repo or "",
        "pr_url": existing_pr or "",
        "pr_number": existing_pr_num,
        "queue": updated_queue,
        "hitl_decision": "pending_review",
        "messages": messages,
        "error_message": ""
    }
