import os
from typing import Dict, Any
from datetime import datetime, timezone
from langchain_core.messages import AIMessage

from graphs.content_creation.utils.paths import normalize_project_path, _resolve_asset_path
from graphs.content_creation.utils.logging import _append_execution_log
from graphs.content_creation.utils.classifiers import classify_gate1_intent, classify_gate2_intent
from graphs.content_creation.adapters import format_gate1_presentation, format_gate2_presentation


async def hitl_image_and_plot_approval_node(state: dict):
    """🛑 HITL GATE 1: Presents 1-shot base image and approved video plot for user review & approval."""
    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir") or (os.path.join(project_dir, topic) if project_dir else ""))
    execution_log_path = state.get("execution_log_path")
    
    image_path = _resolve_asset_path(output_dir, topic, "image", next_version=False)
    video_plot_path = _resolve_asset_path(output_dir, topic, "video_plot", next_version=False)

    summary = format_gate1_presentation(state)

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="🛑 Human-in-the-Loop",
        event_title=f"Gate 1 Presented",
        details={
            "Base Image": image_path,
            "Video Plot": video_plot_path,
            "Status": "Awaiting User Decision"
        },
        log_path=execution_log_path
    )

    return {
        "project_dir": project_dir,
        "output_dir": output_dir,
        "image_path": image_path,
        "video_plot_path": video_plot_path,
        "messages": [AIMessage(content=summary)]
    }
