import os
from typing import Dict, Any
from datetime import datetime, timezone
from langchain_core.messages import AIMessage

from graphs.content_creation.utils.paths import normalize_project_path, _resolve_asset_path
from graphs.content_creation.utils.logging import _append_execution_log
from graphs.content_creation.utils.classifiers import classify_gate1_intent, classify_gate2_intent
from graphs.content_creation.adapters import format_gate1_presentation, format_gate2_presentation


async def clarify_gate1_node(state: dict):
    """Node: Prompts user for clarification when Gate 1 feedback is ambiguous."""
    feedback = state.get("latest_human_feedback", "")
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir", ""))
    msg = (
        f"🛑 **[HITL Gate 1 Clarification Needed]**\n\n"
        f"I received your feedback: *\"{feedback}\"*\n\n"
        f"Please clarify your desired action:\n"
        f"1. **Regenerate Base Image** (character appearance, visual style, or subject adjustments)\n"
        f"2. **Revise Video Plot** (camera motion, timing, or mouth movement script)\n"
        f"3. **Approve & Proceed** to video generation"
    )
    return {
        "project_dir": project_dir,
        "output_dir": output_dir,
        "clarification_question": msg,
        "messages": [AIMessage(content=msg)]
    }
