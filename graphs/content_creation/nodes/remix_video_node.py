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
from tools.remix_video import remix_video

from graphs.content_creation.schemas import PlotAudit, VideoPlot, FinalCopy

async def remix_video_node(state: dict):
    """Step 5: Content Creator overlays audio track and styled text onto visual plate using remix_video."""
    if state.get("error_message"):
        return {}
    topic = state.get("topic", "")
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir", ""))
    raw_video_path = _resolve_asset_path(output_dir, topic, "raw_video", next_version=False)
    video_path = _resolve_asset_path(output_dir, topic, "video", next_version=True)
    video_plot_path = state.get("video_plot_path") or _resolve_asset_path(output_dir, topic, "video_plot", next_version=False)
    plot_content = state.get("video_plot_content", "")
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")

    # Fast-fail if raw visual plate is missing or failed generation
    if not (raw_video_path and os.path.isfile(raw_video_path) and os.path.getsize(raw_video_path) > 0):
        gen_err = state.get("video_generation_error") or f"Raw visual plate not found at '{raw_video_path}'."
        return {
            "video_path": video_path,
            "video_persisted": False,
            "video_generation_error": gen_err
        }

    actions = []
    audio_path = state.get("audio_path")
    overlay_text = state.get("overlay_text", [])
    
    try:
        plot_json_path = video_plot_path.replace(".md", ".json")
        with open(plot_json_path, "r") as pf:
            plot_data = json.load(pf)
            
        if plot_data.get("source_audio") and os.path.exists(plot_data["source_audio"]):
            audio_path = plot_data["source_audio"]
        if plot_data.get("overlay_text"):
            overlay_text = plot_data["overlay_text"]
    except Exception:
        pass

    if not audio_path and output_dir:
        import glob
        cands = glob.glob(os.path.join(output_dir, f"{topic}*.wav"))
        audio_path = cands[0] if cands else os.path.join(output_dir, f"{topic}_wav.wav")
        
    if audio_path:
        actions.append({
            "action": "add_audio",
            "audio_path": audio_path,
            "start_time": 1.5,
            "volume": 1.8,
            "original_volume": 0.6,
            "blend_mode": "blend"
        })
        
    for text in overlay_text:
        actions.append({
            "action": "add_text",
            "text": text,
            "start_time": 1.5,
            "end_time": 3.5,
            "font_size": 110,
            "font_color": "white",
            "border_color": "0x4A3B32",
            "border_width": 8,
            "x": "(w-text_w)/2",
            "y": "h*0.22"
        }) 
    print(f"ContentCreationGraph: Remixing video from {raw_video_path} to {video_path}...")

    remix_err = ""
    try:
        result = await remix_video.ainvoke({
            "video_path": raw_video_path,
            "actions": actions,
            "output_path": video_path,
            "agent_id": "content-creator"
        })
        if "<errors>" in result and "</errors>" in result:
            err_val = result.split("<errors>")[1].split("</errors>")[0].strip()
            if err_val and err_val.lower() != "none":
                remix_err = err_val
        if "<payload>" in result and "</payload>" in result:
            saved = result.split("<payload>")[1].split("</payload>")[0].strip()
            if saved:
                from core.util.config import Config
                codebase_dir = Config().codebase_dir
                if saved.startswith(codebase_dir):
                    video_path = os.path.relpath(saved, codebase_dir)
                else:
                    video_path = saved
    except Exception as e:
        print(f"ContentCreationGraph: Error remixing video: {e}")
        return {"error_message": f"FFmpeg Remix Error: {e}", "failed_node": "remix_video"}

    file_persisted = bool(video_path and os.path.isfile(video_path) and os.path.getsize(video_path) > 0)
    if not file_persisted and not remix_err:
        remix_err = f"Remixed video file not found or empty on disk at '{video_path}'."

    if file_persisted:
        _append_execution_log(
            output_dir=output_dir,
            topic=topic,
            actor="🎬 Content Creator",
            event_title=f"Video Remix & Assembly",
            details={
                "Remixed Video Path": video_path,
                "Source Plate": raw_video_path,
                "Actions Applied": actions,
                "File Status": f"Verified on disk ({os.path.getsize(video_path)} bytes)"
            },
            log_path=execution_log_path
        )
    else:
        _append_execution_log(
            output_dir=output_dir,
            topic=topic,
            actor="🎬 Content Creator",
            event_title=f"Video Remix Failed",
            details={
                "Target Video Path": video_path,
                "Source Plate": raw_video_path,
                "Error": remix_err,
                "File Status": "MISSING / 0 BYTES ON DISK"
            },
            log_path=execution_log_path
        )

    return {
        "video_path": video_path,
        "remix_actions": actions,
        "video_persisted": file_persisted,
        "video_generation_error": remix_err if not file_persisted else ""
    }
