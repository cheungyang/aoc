import os
from typing import Dict, Any
from datetime import datetime, timezone
from langchain_core.messages import AIMessage

from graphs.content_creation.utils.paths import normalize_project_path, _resolve_asset_path
from graphs.content_creation.utils.logging import _append_execution_log
from graphs.content_creation.utils.classifiers import classify_gate1_intent, classify_gate2_intent
from graphs.content_creation.adapters import format_gate1_presentation, format_gate2_presentation


async def process_gate1_feedback_node(state: dict):
    """Processes human feedback at Gate 1, updates asset versioning, and prepares routing decision."""
    feedback = state.get("latest_human_feedback") or state.get("query") or ""
    decision = classify_gate1_intent(feedback)
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir", ""))
    topic = state.get("topic", "")
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")

    history = list(state.get("revision_history") or [])
    history.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "gate": "gate1",
        "feedback": feedback,
        "decision": decision
    })

    updates: Dict[str, Any] = {
        "project_dir": project_dir,
        "output_dir": output_dir,
        "gate1_decision": decision,
        "revision_history": history
    }

    if decision == "revise_image":
        
        _append_execution_log(
            output_dir, topic, "🛑 Human-in-the-Loop",
            f"Gate 1 Decision: REVISE_IMAGE ",
            {"Feedback": feedback},
            execution_log_path
        )

    elif decision == "revise_plot":
        
        _append_execution_log(
            output_dir, topic, "🛑 Human-in-the-Loop",
            f"Gate 1 Decision: REVISE_PLOT ",
            {"Feedback": feedback},
            execution_log_path
        )

    elif decision == "approved":
        _append_execution_log(
            output_dir, topic, "🛑 Human-in-the-Loop",
            "Gate 1 Decision: APPROVED",
            {"Feedback": feedback or "Approved for Video Generation"},
            execution_log_path
        )

    elif decision == "clarify":
        _append_execution_log(
            output_dir, topic, "🛑 Human-in-the-Loop",
            "Gate 1 Decision: CLARIFICATION_REQUIRED",
            {"Feedback": feedback},
            execution_log_path
        )

    return updates
