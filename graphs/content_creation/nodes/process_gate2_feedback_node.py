import os
from typing import Dict, Any
from datetime import datetime, timezone
from langchain_core.messages import AIMessage

from graphs.content_creation.utils.paths import normalize_project_path, _resolve_asset_path
from graphs.content_creation.utils.logging import _append_execution_log
from graphs.content_creation.utils.classifiers import classify_gate1_intent, classify_gate2_intent
from graphs.content_creation.adapters import format_gate1_presentation, format_gate2_presentation


async def process_gate2_feedback_node(state: dict):
    """Processes human feedback at Gate 2, updates versioning, and sets routing decision."""
    feedback = state.get("latest_human_feedback") or ""
    decision = classify_gate2_intent(feedback)
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir", ""))
    topic = state.get("topic", "")
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")
    history = list(state.get("revision_history") or [])
    history.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "gate": "gate2",
        "feedback": feedback,
        "decision": decision
    })

    updates = {
        "project_dir": project_dir,
        "output_dir": output_dir,
        "gate2_decision": decision,
        "revision_history": history
    }

    if decision == "revise_copy":
        _append_execution_log(
            output_dir, topic, "🎉 Human-in-the-Loop",
            f"Gate 2 Decision: REVISE_COPY ",
            {"Feedback": feedback},
            execution_log_path
        )

    elif decision == "revise_remix":
        _append_execution_log(
            output_dir, topic, "🎉 Human-in-the-Loop",
            f"Gate 2 Decision: REVISE_REMIX ",
            {"Feedback": feedback},
            execution_log_path
        )

    elif decision == "revise_video":
        _append_execution_log(
            output_dir, topic, "🎉 Human-in-the-Loop",
            f"Gate 2 Decision: REVISE_VIDEO ",
            {"Feedback": feedback},
            execution_log_path
        )

    elif decision == "approved":
        _append_execution_log(
            output_dir, topic, "🎉 Human-in-the-Loop",
            "Gate 2 Decision: APPROVED (Final Delivery Complete)",
            {"Feedback": feedback or "1-Click Signoff"},
            execution_log_path
        )

    elif decision == "clarify":
        _append_execution_log(
            output_dir, topic, "🎉 Human-in-the-Loop",
            "Gate 2 Decision: CLARIFICATION_REQUIRED",
            {"Feedback": feedback},
            execution_log_path
        )

    return updates
