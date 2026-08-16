import os
import json
from typing import Dict, Any, List
from datetime import datetime, timezone
from langchain_core.messages import AIMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field

from core.loaders.agents_loader import AgentsLoader
from core.agent.agent import Agent
from graphs.content_creation.utils.paths import normalize_project_path, _resolve_asset_path, _resolve_project_doc_path
from graphs.content_creation.utils.logging import _append_execution_log

from tools.extract_video_frames import extract_video_frames
from tools.audio_stream_probe import audio_stream_probe
from tools.video_ocr_validator import video_ocr_validator

from graphs.content_creation.schemas import PlotAudit, VideoPlot, FinalCopy

async def autonomous_debugger_node(state: dict):
    """Dedicated node for Native Exception Routing. Catches system/API/FFmpeg crashes."""
    error = state.get("error_message", "Unknown error")
    failed_node = state.get("failed_node", "unknown")
    topic = state.get("topic", "scene")
    output_dir = state.get("output_dir", "")
    
    print(f"ContentCreationGraph: 🛠️ Autonomous Debugger activated for node '{failed_node}'. Error: {error}")
    
    attempts = state.get("debugger_attempts", 0) + 1
    
    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="🛠️ Autonomous Debugger",
        event_title=f"System Error Recovery (Attempt {attempts})",
        details={
            "Failed Node": failed_node,
            "Error Details": error,
            "Action": "Clearing error state and triggering retry." if attempts < 3 else "Failing graph permanently."
        },
        log_path=state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")
    )
    
    if attempts >= 3:
        return {"debugger_attempts": attempts} # keep error_message intact to halt graph
        
    # Clear error to allow retry
    return {"error_message": "", "debugger_attempts": attempts}
