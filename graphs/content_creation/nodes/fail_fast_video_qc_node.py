import os
from typing import Dict, Any
from datetime import datetime, timezone
from langchain_core.messages import AIMessage

from graphs.content_creation.utils.paths import normalize_project_path, _resolve_asset_path
from graphs.content_creation.utils.logging import _append_execution_log
from graphs.content_creation.utils.classifiers import classify_gate1_intent, classify_gate2_intent
from graphs.content_creation.adapters import format_gate1_presentation, format_gate2_presentation


async def fail_fast_video_qc_node(state: dict):
    """🛑 Headless Fail-Fast: Aborts execution when video generation/QC exhausts retry attempts."""
    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir") or (os.path.join(project_dir, topic) if project_dir else ""))
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")
    attempts = state.get("video_qc_attempts", 0)
    feedback = state.get("video_qc_feedback") or state.get("video_generation_error") or "Video file missing or failed QC checks."
    video_path = state.get("video_path", "")

    error_msg = f"Video QC failed after {attempts} attempts. Root Cause: {feedback}"

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="🛑 System (Fail-Fast)",
        event_title="Video QC Retry Exhaustion — Workflow Aborted",
        details={
            "Target Video Path": video_path,
            "Failed Attempts": attempts,
            "Root Cause": feedback,
            "Status": "ABORTED"
        },
        log_path=execution_log_path
    )

    # Returning an error message which will cause downstream or parent routers to halt
    return {
        "error_message": error_msg
    }
