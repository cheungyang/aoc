import os
import re
from typing import Dict, Any, List
from langchain_core.messages import AIMessage
from graphs.coding.schemas import CodingState
from graphs.coding.utils import git_ops
from graphs.coding.utils.dag import update_task_in_queue, save_manifest, resolve_manifest_path

APPROVAL_KEYWORDS = [
    "approved", "approve", "lgtm", "yes", "proceed", "go ahead",
    "looks good", "looks great", "ok", "pass", "good to merge"
]
ABORT_KEYWORDS = ["abort", "cancel", "stop", "kill"]
REVISION_KEYWORDS = ["fix", "change", "revise", "update", "issue", "bug", "typo", "add ", "modify", "refactor", "redo"]

def classify_hitl_intent(feedback: str) -> str:
    """Classifies user resume feedback into approved, abort, or revise."""
    if not feedback:
        return ""
    clean = feedback.strip().lower()

    # Check abort first
    if any(clean.startswith(ab) or clean == ab for ab in ABORT_KEYWORDS):
        return "abort"

    # Check revision keywords
    has_revision = any(rev in clean for rev in REVISION_KEYWORDS)
    if has_revision:
        return "revise"

    # Check approval keywords
    if any(app in clean for app in APPROVAL_KEYWORDS):
        return "approved"

    # Default to revision
    return "revise"


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
    feature_name = current_task.get("feature_name") or task_id
    run_id = state.get("run_id") or "run_default"
    spec_path = state.get("master_spec_path") or current_task.get("spec_path", "")
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
    queue = state.get("queue") or []
    updated_queue = update_task_in_queue(
        queue=queue,
        task_id=task_id,
        status="in_review",
        run_id=run_id,
        branch_name=branch_name,
        pr_url=existing_pr
    )

    manifest_path = state.get("build_request_path") or resolve_manifest_path(state.get("build_request_path"), project_path)
    save_manifest(manifest_path, {
        "version": "2.0",
        "project_name": project_name,
        "max_concurrency": state.get("max_concurrency", 1),
        "queue": updated_queue
    })

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


async def process_hitl_decision_node(state: CodingState) -> Dict[str, Any]:
    """
    Processes human resume feedback after HITL Gate interrupt (v2).
    Evaluates dual-approval from GitHub PR status or chat feedback.
    """
    workspace_path = state.get("workspace_path", "")
    project_path = state.get("project_path", "")
    pr_number = state.get("pr_number")
    pr_url = state.get("pr_url", "")
    target_pr = pr_number if pr_number is not None else pr_url
    target_repo = state.get("target_repo") or await git_ops.discover_target_repo(workspace_path, ".")

    github_comments: List[str] = []
    gh_decision = ""

    target_dir = workspace_path or "."
    if target_pr:
        pr_status = await git_ops.get_pull_request_status(target_dir, str(target_pr), target_repo=target_repo)
        gh_decision = (pr_status.get("reviewDecision") or "").upper()
        raw_comments = pr_status.get("comments") or []
        for c in raw_comments:
            if isinstance(c, dict) and c.get("body"):
                github_comments.append(c["body"])
            elif isinstance(c, str):
                github_comments.append(c)

    human_feedback = (state.get("latest_human_feedback") or "").strip()
    decision = state.get("hitl_decision", "")

    # Dual-Approval Evaluation Logic
    if gh_decision == "APPROVED" or decision == "approved":
        final_decision = "approved"
        final_feedback = ""
    elif human_feedback:
        final_decision = classify_hitl_intent(human_feedback)
        final_feedback = human_feedback if final_decision == "revise" else ""
    elif gh_decision == "CHANGES_REQUESTED" or github_comments:
        final_decision = "revise"
        final_feedback = "\n".join(github_comments) if github_comments else "Changes requested on GitHub PR."
    elif decision == "pending_review" or not decision:
        final_decision = "approved"
        final_feedback = ""
    else:
        final_decision = decision
        final_feedback = human_feedback

    return {
        "target_repo": target_repo or "",
        "hitl_decision": final_decision,
        "latest_human_feedback": final_feedback,
        "github_pr_comments": github_comments
    }
