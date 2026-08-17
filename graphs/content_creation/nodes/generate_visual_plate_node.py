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
from tools.generate_animation_veo3 import generate_animation_veo3

from graphs.content_creation.schemas import PlotAudit, VideoPlot, FinalCopy

async def generate_visual_plate_node(state: dict):
    """Step 4: Content Creator uses approved motion prompt to generate raw video visual plate."""
    if state.get("error_message"):
        return {}
    topic = state.get("topic", "")
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir", ""))
    raw_video_path = _resolve_asset_path(output_dir, topic, "raw_video", next_version=True)
    image_path = state.get("image_path") or _resolve_asset_path(output_dir, topic, "image", next_version=False)
    video_plot_path = state.get("video_plot_path") or _resolve_asset_path(output_dir, topic, "video_plot", next_version=False)
    plot_content = state.get("video_plot_content", "")
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")

    try:
        plot_json_path = video_plot_path.replace(".md", ".json")
        with open(plot_json_path, "r") as pf:
            plot_data = json.load(pf)
        motion_prompt = plot_data.get("motion_prompt", "Cinematic video scene")
    except Exception:
        motion_prompt = "Cinematic video scene" 
    print(f"ContentCreationGraph: Generating visual plate from {image_path} to {raw_video_path}...")

    gen_error = ""
    try:
        result = await generate_animation_veo3.ainvoke({
            "prompt_text": motion_prompt,
            "image_path": image_path,
            "output_path": raw_video_path,
            "aspect_ratio": "9:16",
            "duration": 6,
            "agent_id": "content-creator"
        })
        if "<errors>" in result and "</errors>" in result:
            err_val = result.split("<errors>")[1].split("</errors>")[0].strip()
            if err_val and err_val.lower() != "none":
                gen_error = err_val
        if "<payload>" in result and "</payload>" in result:
            saved = result.split("<payload>")[1].split("</payload>")[0].strip()
            if saved:
                from core.util.config import Config
                codebase_dir = Config().codebase_dir
                if saved.startswith(codebase_dir):
                    raw_video_path = os.path.relpath(saved, codebase_dir)
                else:
                    raw_video_path = saved
    except Exception as e:
        print(f"ContentCreationGraph: Error generating video: {e}")
        return {"error_message": f"Veo 3 API Error: {e}", "failed_node": "generate_visual_plate"}

    # Explicit Disk Persistence Invariant Check
    file_persisted = bool(raw_video_path and os.path.isfile(raw_video_path) and os.path.getsize(raw_video_path) > 0)
    if not file_persisted and not gen_error:
        gen_error = f"Raw visual plate file not found or empty on disk at '{raw_video_path}' after generation."

    if file_persisted:
        _append_execution_log(
            output_dir=output_dir,
            topic=topic,
            actor="🎬 Content Creator",
            event_title=f"Visual Plate Video Generation",
            details={
                "Visual Plate Path": raw_video_path,
                "Motion Prompt": motion_prompt,
                "Engine": "Google Veo 3",
                "File Status": f"Verified on disk ({os.path.getsize(raw_video_path)} bytes)"
            },
            log_path=execution_log_path
        )
    else:
        _append_execution_log(
            output_dir=output_dir,
            topic=topic,
            actor="🎬 Content Creator",
            event_title=f"Visual Plate Video Generation Failed",
            details={
                "Target Visual Plate Path": raw_video_path,
                "Motion Prompt": motion_prompt,
                "Engine": "Google Veo 3",
                "Error": gen_error,
                "File Status": "MISSING / 0 BYTES ON DISK"
            },
            log_path=execution_log_path
        )

    return {
        "raw_video_path": raw_video_path,
        "video_persisted": file_persisted,
        "video_generation_error": gen_error if not file_persisted else ""
    }
