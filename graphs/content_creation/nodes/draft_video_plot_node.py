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

async def draft_video_plot_node(state: dict):
    """Step 2: Content Creator drafts Video Plot following creator instructions (Structured)."""
    if state.get("error_message"):
        return {}
    topic = state.get("topic", "scene")
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir", ""))
    creator_instructions_path = _resolve_project_doc_path(state.get("creator_instructions_path"), project_dir, "02_Creator_Instructions.md")
    
    # Dual-Publishing paths
    video_plot_path = _resolve_asset_path(output_dir, topic, "video_plot", next_version=True)
    video_plot_json_path = video_plot_path.replace(".md", ".json")
    
    execution_log_path = os.path.join(output_dir, "execution_log.md") if output_dir else ""
    feedback = state.get("video_plot_feedback", "")
    human_feedback = state.get("latest_human_feedback", "")

    image_path = state.get("image_path", "")
    audio_path = state.get("source_audio_path") or (os.path.join(output_dir, f"{topic}_wav.wav") if output_dir else f"{topic}_wav.wav")
    
    # Read the instructions to inject into the prompt
    instructions_text = ""
    try:
        with open(creator_instructions_path, "r", encoding="utf-8") as f:
            instructions_text = f.read()
    except Exception:
        pass

    prompt = (
        f"You are the Content Creator.\n"
        f"--- CREATOR INSTRUCTIONS ---\n{instructions_text}\n----------------------------\n"
        f"Draft the Video Plot for the topic '{topic}' strictly following the template and constraints defined in the instructions.\n\n"
        f"IMPORTANT DATA BINDING:\n"
        f"- Use this exact path for the Source Image field: `{image_path}`\n"
        f"- Use this exact path for the Source Audio field: `{audio_path}`\n"
    )
    if feedback:
        prompt += f"\nPrevious Brand Editor Feedback to fix:\n{feedback}\n"
    if human_feedback and state.get("gate1_decision") == "revise_plot":
        prompt += f"\nHuman HITL Revision Feedback for Video Plot:\n{human_feedback}\n"

    try:
        from core.loaders.agents_loader import AgentsLoader
        from langchain_google_genai import ChatGoogleGenerativeAI
        
        config = AgentsLoader()._agent_configs.get("content-creator", {})
        model_name = config.get("model", "gemini-3.7-flash")
        
        llm = ChatGoogleGenerativeAI(model=model_name).with_structured_output(VideoPlot)
        plot_data: VideoPlot = await llm.ainvoke(prompt)
        
        video_plot_content = plot_data.markdown_content
        
        # Dual Publish
        if video_plot_path:
            os.makedirs(os.path.dirname(video_plot_path), exist_ok=True)
            with open(video_plot_path, "w", encoding="utf-8") as f:
                f.write(video_plot_content)
                
            with open(video_plot_json_path, "w", encoding="utf-8") as f:
                import json
                f.write(json.dumps(plot_data.model_dump(), indent=2))
                
    except Exception as e:
        print(f"ContentCreationGraph: Error saving video plot file: {e}")
        video_plot_content = ""

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="📝 Content Creator",
        event_title=f"Video Plot Drafting",
        details={
            "Video Plot MD Path": video_plot_path,
            "Video Plot JSON Path": video_plot_json_path,
            "Video Plot Content Preview": video_plot_content[:300] + ("..." if len(video_plot_content) > 300 else "")
        },
        log_path=execution_log_path
    )

    return {
        "video_plot_path": video_plot_path,
        "video_plot_content": video_plot_content,
        "overlay_text": plot_data.overlay_text if 'plot_data' in locals() else [],
        "audio_path": plot_data.source_audio if 'plot_data' in locals() else audio_path
    }
