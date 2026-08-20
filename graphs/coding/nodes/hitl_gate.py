from typing import Dict, Any
from graphs.coding.schemas import CodingState

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
    HITL Review Gate Presentation Node:
    Pauses execution at this checkpoint for human review.
    """
    return {
        "hitl_decision": "pending_review"
    }


async def process_hitl_decision_node(state: CodingState) -> Dict[str, Any]:
    """
    Processes human resume feedback after HITL Gate interrupt.
    """
    human_feedback = (state.get("latest_human_feedback") or "").strip()
    decision = state.get("hitl_decision", "")

    if not decision or decision == "pending_review":
        if human_feedback:
            decision = classify_hitl_intent(human_feedback)
        else:
            decision = "approved"

    return {
        "hitl_decision": decision,
        "latest_human_feedback": human_feedback if decision == "revise" else ""
    }
