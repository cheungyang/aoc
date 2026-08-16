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

async def audit_video_plot_node(state: dict):
    """Step 3: Brand Editor audits BOTH the generated Base Image and Video Plot against QC playbook rules (Structured)."""
    if state.get("error_message"):
        return {}
    topic = state.get("topic", "")
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir", ""))
    qc_playbook_path = _resolve_project_doc_path(state.get("qc_playbook_path"), project_dir, "03_QC_Playbook.md")
    image_path = _resolve_asset_path(output_dir, topic, "image", next_version=False)
    image_prompt = state.get("image_prompt", "")
    plot_content = state.get("video_plot_content", "")
    video_plot_path = _resolve_asset_path(output_dir, topic, "video_plot", next_version=False)
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")
    attempts = state.get("video_plot_attempts", 0) + 1
    
    # Audit log paths
    audit_md_path = os.path.join(output_dir, f"{topic}_plot_audit.md") if output_dir else ""
    audit_json_path = os.path.join(output_dir, f"{topic}_plot_audit.json") if output_dir else ""

    if not plot_content and video_plot_path and os.path.isfile(video_plot_path):
        try:
            with open(video_plot_path, "r", encoding="utf-8") as f:
                plot_content = f.read()
        except Exception:
            pass

    playbook_text = ""
    try:
        with open(qc_playbook_path, "r", encoding="utf-8") as f:
            playbook_text = f.read()
    except Exception:
        pass

    prompt = (
        f"You are the Brand Editor.\n"
        f"--- QC PLAYBOOK ---\n{playbook_text}\n-------------------\n"
        f"Audit the Video Plot against the playbook's rules:\n\n"
        f"--- Base Image Context ---\n"
        f"Prompt: {image_prompt}\n"
        f"(The actual image will be audited by a human. Do not audit the visual content, but use this prompt to ensure the plot aligns with the intended concept.)\n"
        f"------------------\n\n"
        f"--- Video Plot ---\n{plot_content}\n------------------\n\n"
        f"Instructions:\n"
        f"1. Evaluate if the Video Plot complies with the QC playbook.\n"
        f"2. If the Video Plot violates rules (pacing, camera movement, forbidden effects, missing elements), set rejection_target to 'plot'.\n"
        f"3. Set is_approved to true ONLY if there are no violations in the Video Plot.\n"
        f"4. Provide a detailed markdown_report with your findings."
    )

    if not image_path or not os.path.exists(image_path):
        is_approved = False
        rejection_target = "image"
        feedback = f"Error: Base Image does not exist at path: {image_path}"
        
        # Dual Publish Audit Report
        if audit_md_path:
            with open(audit_md_path, "w", encoding="utf-8") as f:
                f.write(f"# Plot Audit\n\n{feedback}")
        
        _append_execution_log(
            output_dir=output_dir,
            topic=topic,
            actor="🧐 Brand Editor",
            event_title=f"Video Plot QC Audit (Attempt {attempts})",
            details={
                "Verdict": f"REJECTED (Target: IMAGE)",
                "Audit Notes": feedback
            },
            log_path=execution_log_path
        )
        return {
            "video_plot_qc_passed": is_approved,
            "video_plot_feedback": feedback,
            "qc_rejection_target": rejection_target,
            "video_plot_attempts": attempts
        }

    try:
        from core.loaders.agents_loader import AgentsLoader
        from langchain_google_genai import ChatGoogleGenerativeAI
        
        config = AgentsLoader()._agent_configs.get("brand-editor", {})
        model_name = config.get("model", "gemini-3.7-flash")
        
        llm = ChatGoogleGenerativeAI(model=model_name).with_structured_output(PlotAudit)
        audit_data: PlotAudit = await llm.ainvoke(prompt)
        
        is_approved = audit_data.is_approved
        rejection_target = audit_data.rejection_target
        if is_approved:
            rejection_target = "none"
            
        feedback = audit_data.revision_notes if not is_approved else ""
        
        # Dual Publish Audit Report
        if audit_md_path:
            with open(audit_md_path, "w", encoding="utf-8") as f:
                f.write(audit_data.markdown_report)
            with open(audit_json_path, "w", encoding="utf-8") as f:
                import json
                f.write(json.dumps(audit_data.model_dump(), indent=2))
                
    except Exception as e:
        print(f"ContentCreationGraph: Error generating plot audit: {e}")
        is_approved = False
        rejection_target = "plot"
        feedback = str(e)
        audit_data = None

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="🧐 Brand Editor",
        event_title=f"Video Plot QC Audit (Attempt {attempts})",
        details={
            "Verdict": "APPROVED" if is_approved else f"REJECTED (Target: {rejection_target.upper()})",
            "Audit Notes": feedback
        },
        log_path=execution_log_path
    )

    return {
        "video_plot_qc_passed": is_approved,
        "video_plot_feedback": feedback,
        "qc_rejection_target": rejection_target,
        "video_plot_attempts": attempts
    }
