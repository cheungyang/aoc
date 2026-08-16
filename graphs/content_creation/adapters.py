import os
import re
from typing import Dict, Any, Optional
from langchain_core.messages import AIMessage, HumanMessage

from graphs.content_creation.utils.paths import normalize_project_path, _resolve_project_doc_path, _resolve_asset_path

def prepare_input(query: str, caller: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    """Translates incoming text query / kwargs into initial generic ContentCreationState."""
    if caller and "<caller>" not in query:
        formatted_query = f"<caller>{caller}</caller>\n{query}"
    else:
        formatted_query = query

    project_dir = kwargs.get("project_dir") or kwargs.get("project_path") or kwargs.get("project")
    if not project_dir:
        m_pdir = re.search(r'(?:project_dir|project_path|project)[:=]\s*["\']?([^"\'\s,]+)["\']?', query, re.IGNORECASE)
        if m_pdir:
            project_dir = m_pdir.group(1).strip()
        else:
            project_dir = ""

    output_dir_param = kwargs.get("output_dir") or kwargs.get("output_path") or kwargs.get("output")
    if not output_dir_param:
        m_outdir = re.search(r'(?:output_dir|output_path|output)[:=]\s*["\']?([^"\'\s,]+)["\']?', query, re.IGNORECASE)
        if m_outdir:
            output_dir_param = m_outdir.group(1).strip()
        else:
            output_dir_param = ""

    session_id = kwargs.get("session_id")
    thread_id = kwargs.get("thread_id") or session_id

    topic = kwargs.get("topic") or kwargs.get("word")

    # Check if there is an active checkpointer thread with an existing topic or project_dir
    if thread_id:
        try:
            from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
            cp = SqliteCheckpointer()
            snap = cp.get_tuple({"configurable": {"thread_id": thread_id}})
            if snap and snap.checkpoint and "channel_values" in snap.checkpoint:
                ch = snap.checkpoint["channel_values"]
                if not project_dir and ch.get("project_dir"):
                    project_dir = ch["project_dir"]
                if not output_dir_param and ch.get("output_dir"):
                    output_dir_param = ch["output_dir"]
                if not topic and ch.get("topic"):
                    topic = ch["topic"]
        except Exception:
            pass

    if project_dir:
        project_dir = normalize_project_path(project_dir)

    if not topic:
        m_topic = re.search(r'(?:topic|word)[:=]\s*["\']?([a-zA-Z0-9_ -]+)["\']?', query, re.IGNORECASE)
        if m_topic:
            topic = m_topic.group(1).strip()
        else:
            clean_q = query.replace("<caller>", "").replace("</caller>", "").strip()
            m_create = re.search(
                r'(?:create|generate|make|draft|start)\s+(?:content|video|image|post)?\s*(?:for|about|on)?\s*["\']?([a-zA-Z0-9_-]+)["\']?',
                clean_q,
                re.IGNORECASE
            )
            if m_create and m_create.group(1).lower() not in ["content", "video", "image", "post", "a", "the", "ayla", "me", "this"]:
                topic = m_create.group(1).strip()
            elif clean_q and len(clean_q.split()) <= 2 and not any(w in clean_q.lower() for w in [
                "looking for", "instead of", "change", "revise", "make the", "fix", "update",
                "approved", "looks good", "lgtm", "yes", "no", "proceed", "go ahead"
            ]):
                topic = clean_q
            else:
                topic = "scene"

    topic = str(topic).strip().lower()
    qc_timestamps = kwargs.get("qc_timestamps") or [1.0, 2.5, 4.0]

    if not output_dir_param and project_dir:
        output_dir_param = os.path.join(project_dir, topic)

    if not output_dir_param and not project_dir:
        error_msg = "Missing required project/output path. You must explicitly define where assets should be saved (e.g., project_dir: 'path/to/project' or output_dir: 'path/to/project/words/topic')."
        manifest_path = kwargs.get("manifest_path", "")
        creator_instructions_path = kwargs.get("creator_instructions_path", "")
        qc_playbook_path = kwargs.get("qc_playbook_path", "")
        output_dir = ""
        execution_log_path = ""
        image_path = ""
        video_plot_path = ""
        raw_video_path = ""
        video_path = ""
        copy_path = ""
        audio_path = ""
        overlay_text = ""
    else:
        error_msg = ""
        output_dir = normalize_project_path(output_dir_param)
        manifest_path = _resolve_project_doc_path(kwargs.get("manifest_path"), project_dir, "01_Project_Manifest.md")
        creator_instructions_path = _resolve_project_doc_path(kwargs.get("creator_instructions_path"), project_dir, "02_Creator_Instructions.md")
        qc_playbook_path = _resolve_project_doc_path(kwargs.get("qc_playbook_path"), project_dir, "03_QC_Playbook.md")
        execution_log_path = kwargs.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")

        image_path = kwargs.get("image_path") or _resolve_asset_path(output_dir, topic, "image", next_version=False)
        video_plot_path = _resolve_asset_path(output_dir, topic, "video_plot", next_version=False)
        raw_video_path = _resolve_asset_path(output_dir, topic, "raw_video", next_version=False)
        video_path = _resolve_asset_path(output_dir, topic, "video", next_version=False)
        copy_path = _resolve_asset_path(output_dir, topic, "copy", next_version=False)
        
        audio_path = kwargs.get("audio_path") or (os.path.join(output_dir, f"{topic}_wav.wav") if output_dir else f"{topic}_wav.wav")
        overlay_text = kwargs.get("overlay_text") or kwargs.get("text") or ""

    return {
        "project_dir": project_dir,
        "topic": topic,
        "session_id": session_id,
        "thread_id": thread_id,
        "manifest_path": manifest_path,
        "creator_instructions_path": creator_instructions_path,
        "qc_playbook_path": qc_playbook_path,
        "execution_log_path": execution_log_path,
        "output_dir": output_dir,
        "image_path": image_path,
        "video_plot_path": video_plot_path,
        "raw_video_path": raw_video_path if output_dir else "",
        "video_path": video_path,
        "copy_path": copy_path,
        "audio_path": audio_path,
        "overlay_text": overlay_text,
        "audio_verified": False,
        "remix_actions": [],
        "image_prompt": "",
        "video_plot_content": "",
        "video_plot_qc_passed": False,
        "video_plot_feedback": "",
        "extracted_frames": [],
        "qc_timestamps": qc_timestamps,
        "video_qc_passed": False,
        "video_qc_feedback": "",
        "video_qc_rejection_target": "visual_plate",
        "copy_text": "",
        "final_package": {},
        "gate1_decision": "approved",
        "gate2_decision": "approved",
        "latest_human_feedback": "",
        "revision_history": [],
        "clarification_question": "",
        "video_plot_attempts": 0,
        "max_video_plot_reviews": kwargs.get("max_video_plot_reviews", 3),
        "video_qc_attempts": 0,
        "max_video_reviews": kwargs.get("max_video_reviews", 3),
        "messages": [HumanMessage(content=formatted_query)],
        "query": formatted_query,
        "error_message": error_msg
    }

def format_output(state: Dict[str, Any]) -> str:
    """Extracts final reply text from ContentCreationState reading dynamic versioned asset paths."""
    if isinstance(state, dict):
        if state.get("clarification_question"):
            return state["clarification_question"]
        if state.get("video_qc_attempts", 0) >= state.get("max_video_reviews", 3) and not state.get("video_qc_passed"):
            topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
            video_path = state.get("video_path", "")
            feedback = state.get("video_qc_feedback") or state.get("video_generation_error") or "Video file missing or failed QC checks."
            attempts = state.get("video_qc_attempts", 3)
            return (
                f"🛑 **[HITL INTERVENTION REQUIRED: Video Generation/QC Failed]**\n\n"
                f"- **Topic**: `{topic}`\n"
                f"- **Target Video Path**: `{video_path}`\n"
                f"- **Failed Attempts**: `{attempts}`\n"
                f"- **Root Cause**: {feedback}\n\n"
                f"The workflow has hard-blocked delivery because valid video assets could not be verified on disk.\n\n"
                f"Please choose an action:\n"
                f"1. Reply **'retry'** (or provide updated motion prompt instructions) to attempt video generation again.\n"
                f"2. Reply **'abort'** to stop the workflow."
            )
        if state.get("final_package") and "copy_text" in state["final_package"]:
            return format_gate2_presentation(state)
        if state.get("copy_text"):
            return format_gate2_presentation(state)
        if state.get("video_plot_qc_passed") or (state.get("image_path") and state.get("video_plot_content")):
            return format_gate1_presentation(state)
        if state.get("error_message"):
            err = state["error_message"]
            return err if err.startswith("Content creation failed:") else f"Content creation failed: {err}"
        if "messages" in state and state["messages"]:
            last_msg = state["messages"][-1]
            if hasattr(last_msg, "content"):
                return last_msg.content
            elif isinstance(last_msg, dict) and "content" in last_msg:
                return last_msg["content"]
            return str(last_msg)
    return str(state)


def format_gate1_presentation(state: Dict[str, Any]) -> str:
    """Generates the full markdown presentation string for HITL Gate 1 reading dynamic state paths."""
    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir") or (os.path.join(project_dir, topic) if project_dir else ""))

    image_path = state.get("image_path") or _resolve_asset_path(output_dir, topic, "image", next_version=False)
    video_plot_path = state.get("video_plot_path") or _resolve_asset_path(output_dir, topic, "video_plot", next_version=False)
    plot_content = state.get("video_plot_content", "")
    if not plot_content and video_plot_path and os.path.exists(video_plot_path):
        try:
            with open(video_plot_path, "r", encoding="utf-8") as f:
                plot_content = f.read()
        except Exception:
            pass

    return (
        f"🛑 **[HITL GATE 1: Image & Video Plot Approval Required]**\n\n"
        f"- **Topic**: `{topic}`\n"
        f"- **Base Image**: `{image_path}`\n"
        f"- **Approved Video Plot**: `{video_plot_path}`\n\n"
        f"<images>\n  <image path=\"{image_path}\"/>\n</images>\n\n"
        f"```markdown\n{plot_content}\n```\n\n"
        f"Reply **'approved'** to generate video, or enter revision instructions for the image or video plot."
    )




def format_gate2_presentation(state: Dict[str, Any]) -> str:
    """Generates the full markdown presentation string for HITL Gate 2 reading dynamic state paths."""
    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir") or (os.path.join(project_dir, topic) if project_dir else ""))
    image_path = state.get("image_path") or _resolve_asset_path(output_dir, topic, "image", next_version=False)
    video_plot_path = state.get("video_plot_path") or _resolve_asset_path(output_dir, topic, "video_plot", next_version=False)
    video_path = state.get("video_path") or _resolve_asset_path(output_dir, topic, "video", next_version=False)
    copy_path = state.get("copy_path") or _resolve_asset_path(output_dir, topic, "copy", next_version=False)
    copy_text = state.get("copy_text", "")
    if not copy_text and copy_path and os.path.exists(copy_path):
        try:
            with open(copy_path, "r", encoding="utf-8") as f:
                copy_text = f.read()
        except Exception:
            pass

    video_exists = bool(video_path and os.path.isfile(video_path) and os.path.getsize(video_path) > 0)
    video_tag = f"`{video_path}`" if video_exists else f"`{video_path}` ⚠️ **[MISSING / 0 BYTES ON DISK - CANNOT FINALIZE]**"
    image_exists = bool(image_path and os.path.isfile(image_path) and os.path.getsize(image_path) > 0)
    image_tag = f"`{image_path}`" if image_exists else f"`{image_path}` ⚠️ **[MISSING ON DISK]**"
    copy_exists = bool(copy_path and os.path.isfile(copy_path) and os.path.getsize(copy_path) > 0)
    copy_tag = f"`{copy_path}`" if copy_exists else f"`{copy_path}` ⚠️ **[MISSING ON DISK]**"

    video_xml = f"<videos>\n  <video path=\"{video_path}\"/>\n</videos>\n\n" if video_exists else ""

    return (
        f"🎉 **[HITL GATE 2: Final Package Review & Approval]**\n\n"
        f"**Topic**: `{topic}`\n\n"
        f"### 📦 Deliverables Package\n"
        f"- **Base Image**: {image_tag}\n"
        f"- **Video Plot Doc**: `{video_plot_path}`\n"
        f"- **Master Visual Plate**: {video_tag}\n"
        f"- **Publication Copy File**: {copy_tag}\n\n"
        f"<images>\n  <image path=\"{image_path}\"/>\n</images>\n\n"
        f"{video_xml}"
        f"### 📱 Publication Copy Preview\n"
        f"```markdown\n{copy_text}\n```\n\n"
        f"Reply **'approved'** to finalize delivery, or specify changes for copy, text/audio remix, or video animation."
    )



