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

async def setup_and_generate_image_node(state: dict):
    """Step 1: Content Creator reads project manifest and creator instructions to generate 1-shot base image."""
    if state.get("error_message"):
        return {
            "error_message": state["error_message"],
            "messages": [AIMessage(content=state["error_message"])]
        }

    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir", ""))
    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()

    if not project_dir and not output_dir:
        err = "Missing required project_dir or output_dir. Please provide the project or output path (e.g., project_dir: 'pkm/wiki/software/your-project') to initialize the content creation flow."
        return {
            "error_message": err,
            "messages": [AIMessage(content=err)]
        }

    if not output_dir and project_dir:
        output_dir = normalize_project_path(os.path.join(project_dir, topic))

    manifest_path = _resolve_project_doc_path(state.get("manifest_path"), project_dir, "01_Project_Manifest.md")
    creator_instructions_path = _resolve_project_doc_path(state.get("creator_instructions_path"), project_dir, "02_Creator_Instructions.md")
    qc_playbook_path = _resolve_project_doc_path(state.get("qc_playbook_path"), project_dir, "03_QC_Playbook.md")
    execution_log_path = os.path.join(output_dir, "execution_log.md") if output_dir else ""

    image_path = _resolve_asset_path(output_dir, topic, "image", next_version=True)
    
    os.makedirs(output_dir, exist_ok=True)

    creator = AgentsLoader().get_agent("content-creator")
    prompt = (
        f"You are the Content Creator. Read the project manifest at {manifest_path} and the creator instructions at {creator_instructions_path}.\n"
        f"Generate the 1-shot base image prompt for the topic: '{topic}'.\n"
    )

    human_feedback = state.get("latest_human_feedback", "")
    if human_feedback and state.get("gate1_decision") == "revise_image":
        prompt += f"\nHuman HITL Revision Feedback to incorporate:\n{human_feedback}\n"

    prompt += "\nOutput ONLY the final image generation prompt."

    image_prompt = await creator.execute(prompt, source="subgraph")
    image_prompt = image_prompt.strip().strip('"').strip("'")

    print(f"ContentCreationGraph: Generating base image to {image_path}...")
    try:
        result = await generate_image.ainvoke({
            "prompt": image_prompt,
            "output_path": image_path,
            "agent_id": "content-creator"
        })
        if "<payload>" in result and "</payload>" in result:
            saved = result.split("<payload>")[1].split("</payload>")[0].strip()
            if saved:
                image_path = saved
    except Exception as e:
        print(f"ContentCreationGraph: Error generating image: {e}")

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="🎨 Content Creator",
        event_title=f"Base Image Generation",
        details={
            "Image Path": image_path,
            "Image Prompt": image_prompt,
            "Target Revision": human_feedback if human_feedback and state.get("gate1_decision") == "revise_image" else "Initial 1-Shot Concept"
        },
        log_path=execution_log_path
    )

    return {
        "project_dir": project_dir,
        "topic": topic,
        "manifest_path": manifest_path,
        "creator_instructions_path": creator_instructions_path,
        "qc_playbook_path": qc_playbook_path,
        "execution_log_path": execution_log_path,
        "output_dir": output_dir,
        "image_path": image_path,
        "image_prompt": image_prompt
    }
