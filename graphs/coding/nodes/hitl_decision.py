from typing import Dict, Any, List
from graphs.coding.schemas import CodingState
from graphs.coding.utils import git_ops

APPROVAL_KEYWORDS = [
    "approved", "approve", "lgtm", "yes", "proceed", "go ahead",
    "looks good", "looks great", "ok", "pass", "good to merge"
]
ABORT_KEYWORDS = ["abort", "cancel", "stop", "kill"]
REVISION_KEYWORDS = [
    "fix", "change", "revise", "update", "issue", "bug",
    "typo", "add ", "modify", "refactor", "redo"
]


def classify_hitl_intent(feedback: str) -> str:
    """Classifies user resume feedback into approved, abort, or revise."""
    if not feedback:
        return ""
    clean = feedback.strip().lower()

    # Check abort first
    if any(clean.startswith(ab) or clean == ab for ab in ABORT_KEYWORDS):
        return "abort"

    # Check revision keywords
    if any(rev in clean for rev in REVISION_KEYWORDS):
        return "revise"

    # Check approval keywords
    if any(app in clean for app in APPROVAL_KEYWORDS):
        return "approved"

    # Default to revision
    return "revise"


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
