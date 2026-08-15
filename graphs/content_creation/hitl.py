import os
import re
from datetime import datetime, timezone
from typing import Dict, Any, Literal
from langchain_core.messages import AIMessage
from langgraph.graph import END
from graphs.content_creation.state import (
    ContentCreationState,
    normalize_project_path,
    _resolve_asset_path,
    _append_execution_log
)


# ==========================================
# Intent Classifiers
# ==========================================
def _matches_any_word(words: list[str], text: str) -> bool:
    """Checks if any word/phrase from words matches as a whole word/phrase in text."""
    for w in words:
        if " " in w:
            if w in text:
                return True
        else:
            if re.search(rf"\b{re.escape(w)}\b", text):
                return True
    return False


def classify_gate1_intent(feedback: str) -> str:
    """Classifies human feedback at Gate 1 into approval, image revision, plot revision, or clarification."""
    f = (feedback or "").strip().lower()
    if not f:
        return "approved"

    # 1. Clear approval keywords
    approval_words = ["approved", "approve", "go", "proceed", "yes", "looks good", "lgtm", "pass", "ok", "okay", "continue", "perfect", "good", "yep", "1"]
    if f in approval_words or any(f.startswith(w) for w in ["approved", "approve", "go", "proceed", "looks good", "lgtm", "pass"]):
        return "approved"

    # 2. Topic/attribute keywords
    image_words = [
        "image", "picture", "photo", "face", "hair", "bangs", "character", "clothes", "clothing",
        "outfit", "background", "style", "color", "look", "pose", "eyes", "render", "girl", "puppy",
        "dog", "cat", "fish", "visual", "art", "mascot", "wearing", "jacket", "appearance",
        "costume", "dress", "hat", "shoes", "shirt", "pants", "suit", "illustration"
    ]
    plot_words = [
        "plot", "script", "motion", "camera", "zoom", "pan", "mouth", "lip", "speed",
        "timing", "seconds", "movement", "action", "prompt", "story", "angle", "tracking", "scene", "audio", "duration"
    ]

    has_img = _matches_any_word(image_words, f)
    has_plot = _matches_any_word(plot_words, f)

    if has_img and not has_plot:
        return "revise_image"
    if has_plot and not has_img:
        return "revise_plot"
    if has_img and has_plot:
        return "revise_image"

    # 3. Action verbs
    action_words = [
        "change", "make", "fix", "update", "replace", "redo", "modify", "remove", "add",
        "adjust", "looking for", "instead of", "prefer", "want", "need", "switch"
    ]
    if _matches_any_word(action_words, f):
        return "revise_image"

    return "clarify"


def classify_gate2_intent(feedback: str) -> str:
    """Classifies human feedback at Gate 2 into approval, copy revision, remix revision (text/audio), video plate revision, or clarification."""
    f = (feedback or "").strip().lower()
    if not f:
        return "approved"

    # 1. Clear approval keywords
    approval_words = ["approved", "approve", "go", "proceed", "yes", "looks good", "lgtm", "pass", "ok", "okay", "continue", "perfect", "good", "finalize", "done", "1"]
    if f in approval_words or any(f.startswith(w) for w in ["approved", "approve", "go", "proceed", "looks good", "lgtm", "pass", "finalize"]):
        return "approved"

    # 2. Text/Audio Remix keywords (audio, sound, volume, track, overlay text, subtitle, font)
    if any(phrase in f for phrase in ["text overlay", "overlay text", "video text", "text on video", "text in video", "audio track", "sound volume"]):
        return "revise_remix"

    remix_words = [
        "audio", "sound", "volume", "track", "wav", "mp3", "music", "voice", "loud", "quiet",
        "overlay", "font", "subtitle", "subtitles", "sync", "remix"
    ]
    has_remix = _matches_any_word(remix_words, f)

    # 3. Copy keywords (caption, hashtags, emojis, post wording)
    copy_words = ["copy", "caption", "hashtag", "hashtags", "hook", "wording", "post", "emoji", "emojis", "title", "callout", "instagram", "description", "words"]
    has_copy = _matches_any_word(copy_words, f)

    # 4. Video plate keywords (motion, animation, visual plate, camera, movement, render)
    video_words = ["video", "motion", "animation", "plate", "visual plate", "render", "re-render", "rerun", "clip", "mp4", "camera", "movement", "character", "face"]
    has_video = _matches_any_word(video_words, f)

    if has_remix and not has_video:
        return "revise_remix"
    if has_remix and has_video and not any(w in f for w in ["motion", "animation", "camera", "re-render", "rerun", "character"]):
        return "revise_remix"

    if has_copy and not (has_video or has_remix):
        return "revise_copy"
    if has_video and not (has_copy or has_remix):
        return "revise_video"
    if has_copy and has_video:
        return "revise_copy"

    action_words = ["change", "make", "fix", "update", "replace", "redo", "modify", "remove", "add", "adjust"]
    if _matches_any_word(action_words, f):
        if has_remix:
            return "revise_remix"
        return "revise_copy"

    return "clarify"


# ==========================================
# Presentation Formatters & HITL Gate 1
# ==========================================
def format_gate1_presentation(state: Dict[str, Any]) -> str:
    """Generates the full markdown presentation string for HITL Gate 1 reading dynamic state paths."""
    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir") or (os.path.join(project_dir, topic) if project_dir else ""))

    img_v = state.get("image_version", 1)
    plot_v = state.get("video_plot_version", 1)
    image_path = _resolve_asset_path(state.get("image_path", ""), output_dir, topic, "image", img_v)
    video_plot_path = _resolve_asset_path(state.get("video_plot_path", ""), output_dir, topic, "video_plot", plot_v)
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
        f"- **Base Image (v{img_v})**: `{image_path}`\n"
        f"- **Approved Video Plot (v{plot_v})**: `{video_plot_path}`\n\n"
        f"<images>\n  <image path=\"{image_path}\"/>\n</images>\n\n"
        f"```markdown\n{plot_content}\n```\n\n"
        f"Reply **'approved'** to generate video, or enter revision instructions for the image or video plot."
    )


def format_gate2_presentation(state: Dict[str, Any]) -> str:
    """Generates the full markdown presentation string for HITL Gate 2 reading dynamic state paths."""
    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir") or (os.path.join(project_dir, topic) if project_dir else ""))
    img_v = state.get("image_version", 1)
    plot_v = state.get("video_plot_version", 1)
    video_v = state.get("video_version", 1)
    copy_v = state.get("copy_version", 1)

    image_path = _resolve_asset_path(state.get("image_path", ""), output_dir, topic, "image", img_v)
    video_plot_path = _resolve_asset_path(state.get("video_plot_path", ""), output_dir, topic, "video_plot", plot_v)
    video_path = _resolve_asset_path(state.get("video_path", ""), output_dir, topic, "video", video_v)
    copy_path = _resolve_asset_path(state.get("copy_path", ""), output_dir, topic, "copy", copy_v)
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
        f"- **Base Image (v{img_v})**: {image_tag}\n"
        f"- **Video Plot Doc (v{plot_v})**: `{video_plot_path}`\n"
        f"- **Master Visual Plate (v{video_v})**: {video_tag}\n"
        f"- **Publication Copy File (v{copy_v})**: {copy_tag}\n\n"
        f"<images>\n  <image path=\"{image_path}\"/>\n</images>\n\n"
        f"{video_xml}"
        f"### 📱 Publication Copy Preview\n"
        f"```markdown\n{copy_text}\n```\n\n"
        f"Reply **'approved'** to finalize delivery, or specify changes for copy, text/audio remix, or video animation."
    )


async def hitl_image_and_plot_approval_node(state: ContentCreationState):
    """🛑 HITL GATE 1: Presents 1-shot base image and approved video plot for user review & approval."""
    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir") or (os.path.join(project_dir, topic) if project_dir else ""))
    execution_log_path = state.get("execution_log_path")
    img_v = state.get("image_version", 1)
    plot_v = state.get("video_plot_version", 1)
    image_path = _resolve_asset_path(state.get("image_path", ""), output_dir, topic, "image", img_v)
    video_plot_path = _resolve_asset_path(state.get("video_plot_path", ""), output_dir, topic, "video_plot", plot_v)

    summary = format_gate1_presentation(state)

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="🛑 Human-in-the-Loop",
        event_title=f"Gate 1 Presented (Image v{img_v}, Plot v{plot_v})",
        details={
            "Base Image": image_path,
            "Video Plot": video_plot_path,
            "Status": "Awaiting User Decision"
        },
        log_path=execution_log_path
    )

    return {
        "project_dir": project_dir,
        "output_dir": output_dir,
        "image_path": image_path,
        "video_plot_path": video_plot_path,
        "messages": [AIMessage(content=summary)]
    }


async def process_gate1_feedback_node(state: ContentCreationState):
    """Processes human feedback at Gate 1, updates asset versioning, and prepares routing decision."""
    feedback = state.get("latest_human_feedback") or state.get("query") or ""
    decision = classify_gate1_intent(feedback)
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir", ""))
    topic = state.get("topic", "")
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")

    img_v = state.get("image_version", 1)
    plot_v = state.get("video_plot_version", 1)
    history = list(state.get("revision_history") or [])
    history.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "gate": "gate1",
        "feedback": feedback,
        "decision": decision
    })

    updates = {
        "project_dir": project_dir,
        "output_dir": output_dir,
        "gate1_decision": decision,
        "revision_history": history
    }

    if decision == "revise_image":
        img_v += 1
        plot_v += 1
        updates["image_version"] = img_v
        updates["video_plot_version"] = plot_v
        updates["image_path"] = _resolve_asset_path(state.get("image_path", ""), output_dir, topic, "image", img_v)
        updates["video_plot_path"] = _resolve_asset_path(state.get("video_plot_path", ""), output_dir, topic, "video_plot", plot_v)
        _append_execution_log(
            output_dir, topic, "🛑 Human-in-the-Loop",
            f"Gate 1 Decision: REVISE_IMAGE (v{img_v})",
            {"Feedback": feedback, "New Image Path": updates["image_path"]},
            execution_log_path
        )

    elif decision == "revise_plot":
        plot_v += 1
        updates["video_plot_version"] = plot_v
        updates["video_plot_path"] = _resolve_asset_path(state.get("video_plot_path", ""), output_dir, topic, "video_plot", plot_v)
        _append_execution_log(
            output_dir, topic, "🛑 Human-in-the-Loop",
            f"Gate 1 Decision: REVISE_PLOT (v{plot_v})",
            {"Feedback": feedback, "New Plot Path": updates["video_plot_path"]},
            execution_log_path
        )

    elif decision == "approved":
        _append_execution_log(
            output_dir, topic, "🛑 Human-in-the-Loop",
            "Gate 1 Decision: APPROVED",
            {"Feedback": feedback or "Approved for Video Generation"},
            execution_log_path
        )

    elif decision == "clarify":
        _append_execution_log(
            output_dir, topic, "🛑 Human-in-the-Loop",
            "Gate 1 Decision: CLARIFICATION_REQUIRED",
            {"Feedback": feedback},
            execution_log_path
        )

    return updates


def should_continue_video_plot_audit(state: ContentCreationState):
    """Router: proceeds to HITL Gate 1 if approved or max attempts reached, else routes to setup_and_generate_image or draft_video_plot."""
    if state.get("video_plot_qc_passed"):
        return "hitl_image_and_plot_approval"
    if state.get("video_plot_attempts", 0) >= state.get("max_video_plot_reviews", 3):
        print("ContentCreationGraph: Max video plot reviews reached. Proceeding to HITL Gate 1.")
        return "hitl_image_and_plot_approval"
    
    target = state.get("qc_rejection_target", "plot")
    if target in ["image", "both"]:
        return "setup_and_generate_image"
    return "draft_video_plot"


def should_continue_hitl_gate_1(state: ContentCreationState):
    """Router: routes Gate 1 decision to image generation, plot drafting, clarification, or video rendering."""
    decision = state.get("gate1_decision", "approved")
    if decision == "approved":
        return "generate_visual_plate"
    elif decision == "revise_image":
        return "setup_and_generate_image"
    elif decision == "revise_plot":
        return "draft_video_plot"
    elif decision == "clarify":
        return "clarify_gate1"
    return "generate_visual_plate"


async def clarify_gate1_node(state: ContentCreationState):
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


# ==========================================
# 🎉 HITL GATE 2: hitl_final_package_approval
# ==========================================
async def hitl_final_package_approval_node(state: ContentCreationState):
    """🎉 HITL GATE 2: Presents complete final package to user for 1-click final approval."""
    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir") or (os.path.join(project_dir, topic) if project_dir else ""))
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")
    img_v = state.get("image_version", 1)
    plot_v = state.get("video_plot_version", 1)
    video_v = state.get("video_version", 1)
    copy_v = state.get("copy_version", 1)

    image_path = _resolve_asset_path(state.get("image_path", ""), output_dir, topic, "image", img_v)
    video_plot_path = _resolve_asset_path(state.get("video_plot_path", ""), output_dir, topic, "video_plot", plot_v)
    video_path = _resolve_asset_path(state.get("video_path", ""), output_dir, topic, "video", video_v)
    copy_path = _resolve_asset_path(state.get("copy_path", ""), output_dir, topic, "copy", copy_v)

    final_package = state.get("final_package") or {}
    response_text = format_gate2_presentation(state)

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="🎉 Human-in-the-Loop",
        event_title="Gate 2 Final Package Presented",
        details={
            "Base Image": image_path,
            "Video Master": video_path,
            "Copy Path": copy_path,
            "Status": "Awaiting Final 1-Click Signoff"
        },
        log_path=execution_log_path
    )

    return {
        "project_dir": project_dir,
        "output_dir": output_dir,
        "final_package": final_package,
        "image_path": image_path,
        "video_plot_path": video_plot_path,
        "video_path": video_path,
        "copy_path": copy_path,
        "messages": [AIMessage(content=response_text)]
    }


async def process_gate2_feedback_node(state: ContentCreationState):
    """Processes human feedback at Gate 2, updates versioning, and sets routing decision."""
    feedback = state.get("latest_human_feedback") or state.get("query") or ""
    decision = classify_gate2_intent(feedback)
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir", ""))
    topic = state.get("topic", "")
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")

    copy_v = state.get("copy_version", 1)
    video_v = state.get("video_version", 1)
    history = list(state.get("revision_history") or [])
    history.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "gate": "gate2",
        "feedback": feedback,
        "decision": decision
    })

    updates = {
        "project_dir": project_dir,
        "output_dir": output_dir,
        "gate2_decision": decision,
        "revision_history": history
    }

    if decision == "revise_copy":
        copy_v += 1
        updates["copy_version"] = copy_v
        updates["copy_path"] = _resolve_asset_path(state.get("copy_path", ""), output_dir, topic, "copy", copy_v)
        _append_execution_log(
            output_dir, topic, "🎉 Human-in-the-Loop",
            f"Gate 2 Decision: REVISE_COPY (v{copy_v})",
            {"Feedback": feedback, "New Copy Path": updates["copy_path"]},
            execution_log_path
        )

    elif decision == "revise_remix":
        video_v += 1
        updates["video_version"] = video_v
        updates["video_path"] = _resolve_asset_path(state.get("video_path", ""), output_dir, topic, "video", video_v)
        _append_execution_log(
            output_dir, topic, "🎉 Human-in-the-Loop",
            f"Gate 2 Decision: REVISE_REMIX (v{video_v})",
            {"Feedback": feedback, "New Video Path": updates["video_path"]},
            execution_log_path
        )

    elif decision == "revise_video":
        video_v += 1
        updates["video_version"] = video_v
        updates["video_path"] = _resolve_asset_path(state.get("video_path", ""), output_dir, topic, "video", video_v)
        _append_execution_log(
            output_dir, topic, "🎉 Human-in-the-Loop",
            f"Gate 2 Decision: REVISE_VIDEO (v{video_v})",
            {"Feedback": feedback, "New Video Path": updates["video_path"]},
            execution_log_path
        )

    elif decision == "approved":
        _append_execution_log(
            output_dir, topic, "🎉 Human-in-the-Loop",
            "Gate 2 Decision: APPROVED (Final Delivery Complete)",
            {"Feedback": feedback or "1-Click Signoff"},
            execution_log_path
        )

    elif decision == "clarify":
        _append_execution_log(
            output_dir, topic, "🎉 Human-in-the-Loop",
            "Gate 2 Decision: CLARIFICATION_REQUIRED",
            {"Feedback": feedback},
            execution_log_path
        )

    return updates


def should_continue_video_qc(state: ContentCreationState):
    """Router: proceeds to copywriting if video QC passed, else re-routes to remix_video or generate_visual_plate depending on rejection target, or hard blocks at HITL intervention if max reviews exhausted."""
    if state.get("video_qc_passed"):
        return "draft_and_save_copy"
    if state.get("video_qc_attempts", 0) >= state.get("max_video_reviews", 3):
        print("ContentCreationGraph: Max video QC reviews reached without passing QC. Hard blocking at HITL Video QC Intervention.")
        return "hitl_video_qc_failure_intervention"

    target = state.get("video_qc_rejection_target", "visual_plate")
    if target in ["remix", "audio_text", "text", "audio"]:
        return "remix_video"
    return "generate_visual_plate"


async def hitl_video_qc_failure_intervention_node(state: ContentCreationState):
    """🛑 HITL Intervention: Hard blocks execution when video generation/QC exhausts retry attempts."""
    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir") or (os.path.join(project_dir, topic) if project_dir else ""))
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")
    attempts = state.get("video_qc_attempts", 0)
    feedback = state.get("video_qc_feedback") or state.get("video_generation_error") or "Video file missing or failed QC checks."
    video_path = state.get("video_path", "")

    msg = (
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

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="🛑 Human-in-the-Loop",
        event_title="Video QC Retry Exhaustion — Manual Intervention Required",
        details={
            "Target Video Path": video_path,
            "Failed Attempts": attempts,
            "Root Cause": feedback,
            "Status": "Awaiting Manual Intervention"
        },
        log_path=execution_log_path
    )

    return {
        "project_dir": project_dir,
        "output_dir": output_dir,
        "clarification_question": msg,
        "messages": [AIMessage(content=msg)]
    }


async def process_video_qc_intervention_node(state: ContentCreationState):
    """Processes manual intervention decision after video QC failure."""
    feedback = state.get("latest_human_feedback") or state.get("query") or ""
    f = feedback.strip().lower()
    if any(w in f for w in ["abort", "cancel", "stop", "exit", "quit", "halt"]):
        decision = "abort"
    else:
        decision = "retry"

    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir", ""))
    topic = state.get("topic", "")
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="🛑 Human-in-the-Loop",
        event_title=f"Video QC Intervention Decision: {decision.upper()}",
        details={
            "User Feedback": feedback,
            "Decision": decision
        },
        log_path=execution_log_path
    )

    updates = {
        "latest_human_feedback": feedback,
        "video_qc_attempts": 0  # reset retry attempts counter for new cycle
    }
    if decision == "abort":
        updates["error_message"] = f"Content creation aborted by user after video QC failure: {feedback}"

    return updates


def should_continue_video_qc_intervention(state: ContentCreationState):
    """Router: routes intervention decision to retry generation or remix or abort."""
    feedback = (state.get("latest_human_feedback") or state.get("query") or "").strip().lower()
    if any(w in feedback for w in ["abort", "cancel", "stop", "exit", "quit", "halt"]):
        return END
    target = state.get("video_qc_rejection_target", "visual_plate")
    if target in ["remix", "audio_text", "text", "audio"] or any(w in feedback for w in ["remix", "text", "audio", "font", "sound"]):
        return "remix_video"
    return "generate_visual_plate"


def should_continue_hitl_gate_2(state: ContentCreationState):
    """Router: routes Gate 2 decision to completion, copy revision, remix revision, video re-rendering, or clarification."""
    decision = state.get("gate2_decision", "approved")
    if decision == "approved":
        return END
    elif decision == "revise_copy":
        return "draft_and_save_copy"
    elif decision == "revise_remix":
        return "remix_video"
    elif decision == "revise_video":
        return "generate_visual_plate"
    elif decision == "clarify":
        return "clarify_gate2"
    return END


async def clarify_gate2_node(state: ContentCreationState):
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
