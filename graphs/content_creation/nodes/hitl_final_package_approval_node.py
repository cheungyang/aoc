import os
from typing import Dict, Any
from datetime import datetime, timezone
from langchain_core.messages import AIMessage

from graphs.content_creation.utils.paths import normalize_project_path, _resolve_asset_path
from graphs.content_creation.utils.logging import _append_execution_log
from graphs.content_creation.utils.classifiers import classify_gate1_intent, classify_gate2_intent
from graphs.content_creation.adapters import format_gate1_presentation, format_gate2_presentation


async def hitl_final_package_approval_node(state: dict):
    """🎉 HITL GATE 2: Presents complete final package to user for 1-click final approval."""
    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir") or (os.path.join(project_dir, topic) if project_dir else ""))
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")

    image_path = _resolve_asset_path(output_dir, topic, "image", next_version=False)
    video_plot_path = _resolve_asset_path(output_dir, topic, "video_plot", next_version=False)
    video_path = _resolve_asset_path(output_dir, topic, "video", next_version=False)
    copy_path = _resolve_asset_path(output_dir, topic, "copy", next_version=False)

    final_package = state.get("final_package") or {}
    response_text = format_gate2_presentation(state)

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="🎉 Human-in-the-Loop",
        event_title="Gate 2 Final Package Presented",
        details={
            "Base Image": image_path,
            "Video Master": video_path,
            "Copy Path": copy_path,
            "Status": "Awaiting Final 1-Click Signoff"
        },
        log_path=execution_log_path
    )

    return {
        "project_dir": project_dir,
        "output_dir": output_dir,
        "final_package": final_package,
        "image_path": image_path,
        "video_plot_path": video_plot_path,
        "video_path": video_path,
        "copy_path": copy_path,
        "messages": [AIMessage(content=response_text)]
    }
