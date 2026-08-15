import os
from typing import Dict, Any
from datetime import datetime, timezone
from langchain_core.messages import AIMessage

from graphs.content_creation.utils.paths import normalize_project_path, _resolve_asset_path
from graphs.content_creation.utils.logging import _append_execution_log
from graphs.content_creation.utils.classifiers import classify_gate1_intent, classify_gate2_intent
from graphs.content_creation.adapters import format_gate1_presentation, format_gate2_presentation


async def clarify_gate2_node(state: dict):
    """Node: Prompts user for clarification when Gate 2 feedback is ambiguous."""
    feedback = state.get("latest_human_feedback", "")
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir", ""))
    msg = (
        f"🎉 **[HITL Gate 2 Clarification Needed]**\n\n"
        f"I received your feedback: *\"{feedback}\"*\n\n"
        f"Please clarify your desired action:\n"
        f"1. **Revise Publication Copy** (caption wording, hashtags, or emojis)\n"
        f"2. **Revise Text/Audio Remix** (change text overlay, font size, audio track, volume, or timing)\n"
        f"3. **Re-render Visual Plate Video** (change camera movement or character motion)\n"
        f"4. **Final 1-Click Approval** to complete delivery"
    )
    return {
        "project_dir": project_dir,
        "output_dir": output_dir,
        "clarification_question": msg,
        "messages": [AIMessage(content=msg)]
    }
