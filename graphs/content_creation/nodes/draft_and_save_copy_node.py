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

async def draft_and_save_copy_node(state: dict):
    """Step 6: Content Creator drafts copy (Structured), and saves to copy_path."""
    if state.get("error_message"):
        return {}
    topic = state.get("topic", "")
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir", ""))
    creator_instructions_path = _resolve_project_doc_path(state.get("creator_instructions_path"), project_dir, "02_Creator_Instructions.md")
    qc_playbook_path = _resolve_project_doc_path(state.get("qc_playbook_path"), project_dir, "03_QC_Playbook.md")
    
    # Dual-Publishing paths
    copy_path = _resolve_asset_path(output_dir, topic, "copy", next_version=True)
    copy_json_path = copy_path.replace(".md", ".json") if copy_path else ""
    
    video_path = _resolve_asset_path(output_dir, topic, "video", next_version=False)
    image_path = _resolve_asset_path(output_dir, topic, "image", next_version=False)
    video_plot_path = _resolve_asset_path(output_dir, topic, "video_plot", next_version=False)
    extracted_frames = state.get("extracted_frames", [])
    execution_log_path = os.path.join(output_dir, "execution_log.md") if output_dir else ""
    human_feedback = state.get("latest_human_feedback", "")

    instructions_text = ""
    try:
        with open(creator_instructions_path, "r", encoding="utf-8") as f:
            instructions_text = f.read()
    except Exception:
        pass

    playbook_text = ""
    try:
        with open(qc_playbook_path, "r", encoding="utf-8") as f:
            playbook_text = f.read()
    except Exception:
        pass

    creator_prompt = (
        f"You are the Content Creator.\n"
        f"--- CREATOR INSTRUCTIONS ---\n{instructions_text}\n--------------------------\n"
        f"--- QC PLAYBOOK ---\n{playbook_text}\n-------------------\n"
        f"Draft the publication copy / captions for the topic '{topic}'.\n"
        f"Adhere strictly to both the creator instructions and the QC playbook.\n"
        f"Output the finalized title, body, hashtags, and the complete markdown presentation."
    )
    
    if human_feedback and state.get("gate2_decision") == "revise_copy":
        creator_prompt += f"\nHuman Revision Notes to apply:\n{human_feedback}\n"

    try:
        from core.loaders.agents_loader import AgentsLoader
        from langchain_google_genai import ChatGoogleGenerativeAI
        
        config = AgentsLoader()._agent_configs.get("content-creator", {})
        model_name = config.get("model", "gemini-3.7-flash")
        
        llm = ChatGoogleGenerativeAI(model=model_name).with_structured_output(FinalCopy)
        copy_data: FinalCopy = await llm.ainvoke(creator_prompt)
        
        polished_copy = copy_data.markdown_content
        
        # Dual Publish
        if copy_path:
            os.makedirs(os.path.dirname(copy_path), exist_ok=True)
            with open(copy_path, "w", encoding="utf-8") as f:
                f.write(polished_copy)
            with open(copy_json_path, "w", encoding="utf-8") as f:
                import json
                f.write(json.dumps(copy_data.model_dump(), indent=2))
                
    except Exception as e:
        print(f"ContentCreationGraph: Error saving copy to {copy_path}: {e}")
        polished_copy = ""

    final_package = {
        "project_dir": project_dir,
        "topic": topic,
        "output_dir": output_dir,
        "image_path": image_path,
        "video_plot_path": video_plot_path,
        "video_path": video_path,
        "copy_path": copy_path,
        "extracted_frames": extracted_frames,
        "copy_text": polished_copy
    }

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="📱 Content Creator",
        event_title=f"Publication Copy Drafted",
        details={
            "Copy MD Path": copy_path,
            "Copy JSON Path": copy_json_path,
            "Polished Copy": polished_copy
        },
        log_path=execution_log_path
    )

    return {
        "copy_text": polished_copy,
        "final_package": final_package,
        "copy_path": copy_path
    }
