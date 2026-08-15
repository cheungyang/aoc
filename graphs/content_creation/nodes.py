import os
from typing import Dict, Any, Literal
from langchain_core.messages import AIMessage
from core.loaders.agents_loader import AgentsLoader
from tools.generate_image import generate_image
from tools.generate_animation_veo3 import generate_animation_veo3
from tools.remix_video import remix_video, _has_audio_stream, _get_ffmpeg_executable
from tools.extract_video_frames import extract_video_frames
from graphs.content_creation.state import (
    ContentCreationState,
    normalize_project_path,
    _resolve_project_doc_path,
    _resolve_asset_path,
    _append_execution_log,
    _extract_motion_prompt_from_plot,
    _extract_remix_actions_from_plot
)


# ==========================================
# 1. Node 1: setup_and_generate_image
# ==========================================
async def setup_and_generate_image_node(state: ContentCreationState):
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

    image_version = state.get("image_version") or 1
    video_plot_version = state.get("video_plot_version") or 1
    video_version = state.get("video_version") or 1
    copy_version = state.get("copy_version") or 1

    image_path = _resolve_asset_path(state.get("image_path", ""), output_dir, topic, "image", image_version)
    video_plot_path = _resolve_asset_path(state.get("video_plot_path", ""), output_dir, topic, "video_plot", video_plot_version)
    video_path = _resolve_asset_path(state.get("video_path", ""), output_dir, topic, "video", video_version)
    copy_path = _resolve_asset_path(state.get("copy_path", ""), output_dir, topic, "copy", copy_version)

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

    print(f"ContentCreationGraph: Generating base image (v{image_version}) to {image_path}...")
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
        event_title=f"Base Image Generation (v{image_version})",
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
        "image_version": image_version,
        "video_plot_version": video_plot_version,
        "video_version": video_version,
        "copy_version": copy_version,
        "image_path": image_path,
        "video_plot_path": video_plot_path,
        "video_path": video_path,
        "copy_path": copy_path,
        "image_prompt": image_prompt
    }


# ==========================================
# 2. Node 2: draft_video_plot
# ==========================================
async def draft_video_plot_node(state: ContentCreationState):
    """Step 2: Content Creator drafts Video Plot Markdown following creator instructions."""
    if state.get("error_message"):
        return {}
    topic = state.get("topic", "scene")
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir", ""))
    creator_instructions_path = _resolve_project_doc_path(state.get("creator_instructions_path"), project_dir, "02_Creator_Instructions.md")
    video_plot_version = state.get("video_plot_version") or 1
    video_plot_path = _resolve_asset_path(state.get("video_plot_path", ""), output_dir, topic, "video_plot", video_plot_version)
    execution_log_path = os.path.join(output_dir, "execution_log.md") if output_dir else ""
    feedback = state.get("video_plot_feedback", "")
    human_feedback = state.get("latest_human_feedback", "")

    creator = AgentsLoader().get_agent("content-creator")
    image_path = state.get("image_path", "")
    audio_path = os.path.join(output_dir, f"{topic}_wav.wav") if output_dir else f"{topic}_wav.wav"
    
    prompt = (
        f"You are the Content Creator. Read {creator_instructions_path}.\n"
        f"Draft the Video Plot Markdown for the topic '{topic}' strictly following the template and constraints defined in the instructions.\n\n"
        f"IMPORTANT DATA BINDING:\n"
        f"- Use this exact path for the Source Image field: `{image_path}`\n"
        f"- Use this exact path for the Source Audio field: `{audio_path}`\n"
    )
    if feedback:
        prompt += f"\nPrevious Brand Editor Feedback to fix:\n{feedback}\n"
    if human_feedback and state.get("gate1_decision") == "revise_plot":
        prompt += f"\nHuman HITL Revision Feedback for Video Plot:\n{human_feedback}\n"

    prompt += "\nOutput the complete markdown document directly."

    video_plot_content = await creator.execute(prompt, source="subgraph")
    video_plot_content = video_plot_content.strip()

    try:
        if video_plot_path:
            os.makedirs(os.path.dirname(video_plot_path), exist_ok=True)
            with open(video_plot_path, "w", encoding="utf-8") as f:
                f.write(video_plot_content)
    except Exception as e:
        print(f"ContentCreationGraph: Error saving video plot file: {e}")

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="📝 Content Creator",
        event_title=f"Video Plot Drafting (v{video_plot_version})",
        details={
            "Video Plot Path": video_plot_path,
            "Video Plot Content Preview": video_plot_content[:300] + ("..." if len(video_plot_content) > 300 else "")
        },
        log_path=execution_log_path
    )

    return {
        "project_dir": project_dir,
        "output_dir": output_dir,
        "video_plot_version": video_plot_version,
        "video_plot_content": video_plot_content,
        "video_plot_path": video_plot_path,
        "video_plot_qc_passed": False
    }


# ==========================================
# 3. Node 3: audit_video_plot (Dual-Asset QC)
# ==========================================
async def audit_video_plot_node(state: ContentCreationState):
    """Step 3: Brand Editor audits BOTH the generated Base Image and Video Plot against QC playbook rules."""
    if state.get("error_message"):
        return {}
    topic = state.get("topic", "")
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir", ""))
    qc_playbook_path = _resolve_project_doc_path(state.get("qc_playbook_path"), project_dir, "03_QC_Playbook.md")
    image_path = _resolve_asset_path(state.get("image_path", ""), output_dir, topic, "image", state.get("image_version", 1))
    image_prompt = state.get("image_prompt", "")
    plot_content = state.get("video_plot_content", "")
    video_plot_path = _resolve_asset_path(state.get("video_plot_path", ""), output_dir, topic, "video_plot", state.get("video_plot_version", 1))
    execution_log_path = os.path.join(output_dir, "execution_log.md") if output_dir else ""
    attempts = state.get("video_plot_attempts", 0) + 1
    img_version = state.get("image_version", 1)
    plot_version = state.get("video_plot_version", 1)

    if not plot_content and video_plot_path and os.path.isfile(video_plot_path):
        try:
            with open(video_plot_path, "r", encoding="utf-8") as f:
                plot_content = f.read()
        except Exception:
            pass

    editor = AgentsLoader().get_agent("brand-editor")
    prompt = (
        f"You are the Brand Editor. Read the QC playbook at {qc_playbook_path}.\n"
        f"Audit BOTH the generated Base Image and the Video Plot against the playbook's rules:\n\n"
        f"--- Base Image ---\n"
        f"Path: {image_path}\n"
        f"Prompt: {image_prompt}\n"
        f"------------------\n\n"
        f"--- Video Plot ---\n{plot_content}\n------------------\n\n"
        f"Instructions:\n"
        f"1. If BOTH the Base Image and Video Plot comply with the QC playbook, reply 'VERDICT: APPROVED' on the first line.\n"
        f"2. If the Base Image violates rules (style inconsistency, anatomical defects, wrong visual concept), reply 'VERDICT: REJECTED TARGET: IMAGE' with required revisions.\n"
        f"3. If the Video Plot violates rules (pacing, camera movement, forbidden effects), reply 'VERDICT: REJECTED TARGET: PLOT' with required revisions.\n"
        f"4. If BOTH violate rules, reply 'VERDICT: REJECTED TARGET: BOTH' with required revisions."
    )

    response = await editor.execute(prompt, source="subgraph")
    resp_upper = response.upper()
    is_approved = "VERDICT: APPROVED" in resp_upper or ("APPROVED" in resp_upper and "REJECTED" not in resp_upper)
    feedback = "" if is_approved else response

    rejection_target: Literal["image", "plot", "both"] = "plot"
    if not is_approved:
        if "TARGET: IMAGE" in resp_upper:
            rejection_target = "image"
        elif "TARGET: BOTH" in resp_upper:
            rejection_target = "both"
        elif "TARGET: PLOT" in resp_upper:
            rejection_target = "plot"
        else:
            fb_lower = feedback.lower()
            if any(w in fb_lower for w in ["image", "character", "face", "hair", "art style", "visual plate"]) and not any(w in fb_lower for w in ["plot", "motion prompt", "camera", "zoom"]):
                rejection_target = "image"
            else:
                rejection_target = "plot"

    if not is_approved and attempts < state.get("max_video_plot_reviews", 3):
        if rejection_target in ["image", "both"]:
            img_version += 1
        if rejection_target in ["plot", "both"]:
            plot_version += 1

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="🧐 Brand Editor",
        event_title=f"Dual-Asset QC Audit (Attempt {attempts})",
        details={
            "Verdict": "APPROVED" if is_approved else f"REJECTED (Target: {rejection_target.upper()})",
            "Audit Notes": response.strip(),
            "Next Image Version": f"v{img_version}" if not is_approved and rejection_target in ["image", "both"] else f"v{state.get('image_version', 1)}",
            "Next Plot Version": f"v{plot_version}" if not is_approved and rejection_target in ["plot", "both"] else f"v{state.get('video_plot_version', 1)}"
        },
        log_path=execution_log_path
    )

    return {
        "video_plot_qc_passed": is_approved,
        "video_plot_feedback": feedback,
        "qc_rejection_target": rejection_target,
        "video_plot_attempts": attempts,
        "image_version": img_version,
        "video_plot_version": plot_version
    }


# ==========================================
# 4. Node 4: generate_visual_plate
# ==========================================
async def generate_visual_plate_node(state: ContentCreationState):
    """Step 4: Content Creator uses approved motion prompt to generate raw video visual plate."""
    if state.get("error_message"):
        return {}
    topic = state.get("topic", "")
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir", ""))
    video_version = state.get("video_version") or 1
    raw_video_path = _resolve_asset_path(state.get("raw_video_path", ""), output_dir, topic, "raw_video", video_version)
    image_path = _resolve_asset_path(state.get("image_path", ""), output_dir, topic, "image", state.get("image_version", 1))
    plot_content = state.get("video_plot_content", "")
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")

    motion_prompt = _extract_motion_prompt_from_plot(plot_content, state)
    print(f"ContentCreationGraph: Generating visual plate (v{video_version}) from {image_path} to {raw_video_path}...")

    gen_error = ""
    try:
        result = await generate_animation_veo3.ainvoke({
            "prompt_text": motion_prompt,
            "image_path": image_path,
            "output_path": raw_video_path,
            "aspect_ratio": "9:16",
            "duration": 5,
            "agent_id": "content-creator"
        })
        if "<errors>" in result and "</errors>" in result:
            err_val = result.split("<errors>")[1].split("</errors>")[0].strip()
            if err_val and err_val.lower() != "none":
                gen_error = err_val
        if "<payload>" in result and "</payload>" in result:
            saved = result.split("<payload>")[1].split("</payload>")[0].strip()
            if saved:
                raw_video_path = saved
    except Exception as e:
        gen_error = str(e)
        print(f"ContentCreationGraph: Error generating visual plate: {e}")

    # Explicit Disk Persistence Invariant Check
    file_persisted = bool(raw_video_path and os.path.isfile(raw_video_path) and os.path.getsize(raw_video_path) > 0)
    if not file_persisted and not gen_error:
        gen_error = f"Raw visual plate file not found or empty on disk at '{raw_video_path}' after generation."

    if file_persisted:
        _append_execution_log(
            output_dir=output_dir,
            topic=topic,
            actor="🎬 Content Creator",
            event_title=f"Visual Plate Video Generation (v{video_version})",
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
            event_title=f"Visual Plate Video Generation Failed (v{video_version})",
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
        "project_dir": project_dir,
        "output_dir": output_dir,
        "video_version": video_version,
        "raw_video_path": raw_video_path,
        "video_persisted": file_persisted,
        "video_generation_error": gen_error if not file_persisted else ""
    }


# ==========================================
# 5. Node 5: remix_video
# ==========================================
async def remix_video_node(state: ContentCreationState):
    """Step 5: Content Creator overlays audio track and styled text onto visual plate using remix_video."""
    if state.get("error_message"):
        return {}
    topic = state.get("topic", "")
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir", ""))
    video_version = state.get("video_version") or 1
    raw_video_path = _resolve_asset_path(state.get("raw_video_path", ""), output_dir, topic, "raw_video", video_version)
    video_path = _resolve_asset_path(state.get("video_path", ""), output_dir, topic, "video", video_version)
    plot_content = state.get("video_plot_content", "")
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")

    # Fast-fail if raw visual plate is missing or failed generation
    if not (raw_video_path and os.path.isfile(raw_video_path) and os.path.getsize(raw_video_path) > 0):
        gen_err = state.get("video_generation_error") or f"Raw visual plate not found at '{raw_video_path}'."
        return {
            "project_dir": project_dir,
            "output_dir": output_dir,
            "raw_video_path": raw_video_path,
            "video_path": video_path,
            "video_persisted": False,
            "video_generation_error": gen_err
        }

    actions = _extract_remix_actions_from_plot(plot_content, state)
    print(f"ContentCreationGraph: Remixing video (v{video_version}) from {raw_video_path} to {video_path}...")

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
                video_path = saved
    except Exception as e:
        remix_err = str(e)
        print(f"ContentCreationGraph: Error remixing video: {e}")

    file_persisted = bool(video_path and os.path.isfile(video_path) and os.path.getsize(video_path) > 0)
    if not file_persisted and not remix_err:
        remix_err = f"Remixed video file not found or empty on disk at '{video_path}'."

    if file_persisted:
        _append_execution_log(
            output_dir=output_dir,
            topic=topic,
            actor="🎬 Content Creator",
            event_title=f"Video Remix & Assembly (v{video_version})",
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
            event_title=f"Video Remix Failed (v{video_version})",
            details={
                "Target Video Path": video_path,
                "Source Plate": raw_video_path,
                "Error": remix_err,
                "File Status": "MISSING / 0 BYTES ON DISK"
            },
            log_path=execution_log_path
        )

    return {
        "project_dir": project_dir,
        "output_dir": output_dir,
        "raw_video_path": raw_video_path,
        "video_path": video_path,
        "remix_actions": actions,
        "video_persisted": file_persisted,
        "video_generation_error": remix_err if not file_persisted else ""
    }


# ==========================================
# 6. Node 6: extract_and_qc_frames (Dual Text & Audio QC)
# ==========================================
async def extract_and_qc_frames_node(state: ContentCreationState):
    """Step 6: Brand Editor checks audio stream, extracts video frames, and audits text overlay & visual fidelity."""
    if state.get("error_message"):
        return {}
    topic = state.get("topic", "")
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir", ""))
    video_version = state.get("video_version", 1)
    video_path = _resolve_asset_path(state.get("video_path", ""), output_dir, topic, "video", video_version)
    qc_playbook_path = _resolve_project_doc_path(state.get("qc_playbook_path"), project_dir, "03_QC_Playbook.md")
    execution_log_path = os.path.join(output_dir, "execution_log.md") if output_dir else ""
    qc_timestamps = state.get("qc_timestamps") or [1.0, 2.0, 2.5, 3.5, 4.0]
    frames_dir = os.path.join(output_dir, "frames") if output_dir else "frames"
    attempts = state.get("video_qc_attempts", 0) + 1
    max_reviews = state.get("max_video_reviews", 3)

    # 1. Fast-Fail Diagnosis: Check if video exists and is non-empty on disk
    file_persisted = bool(video_path and os.path.isfile(video_path) and os.path.getsize(video_path) > 0)
    raw_video_path = _resolve_asset_path(state.get("raw_video_path", ""), output_dir, topic, "raw_video", video_version)
    raw_persisted = bool(raw_video_path and os.path.isfile(raw_video_path) and os.path.getsize(raw_video_path) > 0)

    if not file_persisted:
        gen_err = state.get("video_generation_error") or "Remixed video file not found or 0 bytes on disk."
        root_cause = f"Video file missing or empty on disk at '{video_path}'. ({gen_err})"
        next_video_version = video_version + 1
        rejection_target: Literal["visual_plate", "remix", "both"] = "remix" if raw_persisted else "visual_plate"
        action_taken = (
            f"Re-routed to Remix Video for audio/text re-assembly (v{next_video_version})."
            if (rejection_target == "remix" and attempts < max_reviews) else (
                f"Re-routed to Content Creator for fresh visual plate render (v{next_video_version})."
                if attempts < max_reviews else
                "Max retry attempts reached. Hard blocking at HITL Video QC Intervention."
            )
        )

        _append_execution_log(
            output_dir=output_dir,
            topic=topic,
            actor="🧐 Brand Editor",
            event_title=f"Video Keyframe QC Audit (Attempt {attempts})",
            details={
                "Verdict": "REJECTED",
                "Extracted Frames Count": 0,
                "Rejection Target": rejection_target.upper(),
                "Specific Root Cause": root_cause,
                "Action Taken": action_taken
            },
            log_path=execution_log_path
        )

        return {
            "project_dir": project_dir,
            "output_dir": output_dir,
            "extracted_frames": [],
            "video_qc_passed": False,
            "video_qc_feedback": root_cause,
            "video_qc_rejection_target": rejection_target,
            "video_qc_attempts": attempts,
            "video_version": next_video_version
        }

    # 2. Automated Audio Stream Integrity Verification
    ffmpeg_exe = _get_ffmpeg_executable()
    audio_detected = _has_audio_stream(ffmpeg_exe, video_path) if (ffmpeg_exe and file_persisted) else False
    audio_status_str = "Audio stream verified present in remixed video file." if audio_detected else "WARNING: No audio stream detected in remixed video file."

    # 3. Extract frames from verified video file
    if output_dir:
        os.makedirs(frames_dir, exist_ok=True)
    extracted_frames = []

    try:
        res = await extract_video_frames.ainvoke({
            "video_path": video_path,
            "timestamps": qc_timestamps,
            "output_dir": frames_dir,
            "agent_id": "brand-editor"
        })
        if "<payload>" in res and "</payload>" in res:
            payload = res.split("<payload>")[1].split("</payload>")[0].strip()
            extracted_frames = [p.strip() for p in payload.split("\n") if p.strip()]
    except Exception as e:
        print(f"ContentCreationGraph: Error extracting video frames: {e}")

    if not extracted_frames and os.path.exists(frames_dir):
        extracted_frames = [
            os.path.join(frames_dir, f)
            for f in sorted(os.listdir(frames_dir))
            if f.endswith((".jpg", ".png", ".jpeg"))
        ]

    # Handle frame extraction failure
    if not extracted_frames:
        root_cause = f"Failed to extract keyframes from video at '{video_path}'."
        next_video_version = video_version + 1
        action_taken = (
            f"Re-routed to Content Creator for fresh visual plate render (v{next_video_version})."
            if attempts < max_reviews else
            "Max retry attempts reached. Hard blocking at HITL Video QC Intervention."
        )

        _append_execution_log(
            output_dir=output_dir,
            topic=topic,
            actor="🧐 Brand Editor",
            event_title=f"Video Keyframe QC Audit (Attempt {attempts})",
            details={
                "Verdict": "REJECTED",
                "Extracted Frames Count": 0,
                "Audio Stream": audio_status_str,
                "Rejection Target": "VISUAL_PLATE",
                "Specific Root Cause": root_cause,
                "Action Taken": action_taken
            },
            log_path=execution_log_path
        )

        return {
            "project_dir": project_dir,
            "output_dir": output_dir,
            "extracted_frames": [],
            "audio_verified": audio_detected,
            "video_qc_passed": False,
            "video_qc_feedback": root_cause,
            "video_qc_rejection_target": "visual_plate",
            "video_qc_attempts": attempts,
            "video_version": next_video_version
        }

    # 4. Brand Editor LLM Multi-Modal Audit on Keyframes, Text Visibility, and Audio Status
    editor = AgentsLoader().get_agent("brand-editor")
    frames_list_str = "\n".join([f"- Frame {i+1} ({qc_timestamps[i] if i < len(qc_timestamps) else ''}s): {f}" for i, f in enumerate(extracted_frames)])

    prompt = (
        f"You are the Brand Editor. Read {qc_playbook_path}.\n"
        f"Audit the remixed video deliverables for the topic '{topic}'.\n\n"
        f"--- Video Deliverables ---\n"
        f"Video Path: {video_path}\n"
        f"Audio Stream Verification: {audio_status_str}\n"
        f"Extracted Keyframes:\n{frames_list_str}\n"
        f"--------------------------\n\n"
        f"QC Audit Requirements:\n"
        f"1. Visual Plate Fidelity: Check for character consistency, smooth motion, and absence of visual artifacts or distortions.\n"
        f"2. Text Overlay Verification: Verify whether the text overlay is clearly seen in the video keyframes, legible, and properly positioned.\n"
        f"3. Audio Verification: Check that the external audio stream is verified and present ({audio_status_str}).\n\n"
        f"Instructions:\n"
        f"1. If all visual plate, text overlay, and audio criteria are met, reply 'VERDICT: APPROVED' on the first line.\n"
        f"2. If the raw visual plate has motion or video generation defects (distortions, warping, anatomical defects, bad motion, character mismatch, prompt bleed), reply 'VERDICT: REJECTED TARGET: VISUAL_PLATE' with specific revision notes.\n"
        f"3. If the visual plate is fine, but there is an audio or text overlay insertion defect (text missing, wrong font, misaligned/uncentered, audio missing, volume or sync issue), reply 'VERDICT: REJECTED TARGET: REMIX' with specific revision notes.\n"
        f"4. If BOTH the visual plate and text/audio have defects, reply 'VERDICT: REJECTED TARGET: BOTH' with specific revision notes."
    )

    response = await editor.execute(prompt, source="subgraph")
    resp_upper = response.upper()
    is_approved = ("VERDICT: APPROVED" in resp_upper or ("APPROVED" in resp_upper and "REJECTED" not in resp_upper)) and audio_detected

    rejection_target: Literal["visual_plate", "remix", "both"] = "visual_plate"
    if not is_approved:
        if "TARGET: REMIX" in resp_upper or "TARGET: TEXT" in resp_upper or "TARGET: AUDIO" in resp_upper:
            rejection_target = "remix"
        elif "TARGET: BOTH" in resp_upper:
            rejection_target = "both"
        elif "TARGET: VISUAL_PLATE" in resp_upper or "TARGET: VIDEO" in resp_upper:
            rejection_target = "visual_plate"
        elif not audio_detected and "VERDICT: APPROVED" in resp_upper:
            rejection_target = "remix"
        else:
            fb_lower = response.lower()
            remix_keywords = ["text", "font", "overlay", "caption", "word", "audio", "sound", "volume", "track", "wav", "sync"]
            video_keywords = ["motion", "plate", "render", "character", "distortion", "face", "body", "camera", "movement", "animation", "veo", "hallucination"]
            if any(w in fb_lower for w in remix_keywords) and not any(w in fb_lower for w in video_keywords):
                rejection_target = "remix"
            else:
                rejection_target = "visual_plate"

    if not audio_detected and is_approved:
        is_approved = False
        rejection_target = "remix"
        feedback = f"QC Rejection: No audio stream detected in remixed video '{video_path}'.\n{response.strip()}"
    else:
        feedback = "" if is_approved else response.strip()

    next_video_version = video_version if is_approved else video_version + 1
    if is_approved:
        action_taken = "Proceeding to publication copywriting."
    elif attempts >= max_reviews:
        action_taken = "Max retry attempts reached. Hard blocking at HITL Video QC Intervention."
    elif rejection_target == "remix":
        action_taken = f"Re-routed to Remix Video for audio/text re-assembly (v{next_video_version})."
    else:
        action_taken = f"Re-routed to Content Creator for fresh visual plate render (v{next_video_version})."

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="🧐 Brand Editor",
        event_title=f"Video Dual Text/Audio Keyframe QC Audit (Attempt {attempts})",
        details={
            "Verdict": "APPROVED" if is_approved else "REJECTED",
            "Extracted Frames Count": len(extracted_frames),
            "Audio Stream Status": audio_status_str,
            "Rejection Target": rejection_target.upper() if not is_approved else "NONE",
            "Specific Root Cause": response.strip() if not is_approved else "All visual, text overlay, and audio criteria satisfied.",
            "Action Taken": action_taken
        },
        log_path=execution_log_path
    )

    return {
        "project_dir": project_dir,
        "output_dir": output_dir,
        "extracted_frames": extracted_frames,
        "audio_verified": audio_detected,
        "video_qc_passed": is_approved,
        "video_qc_feedback": feedback,
        "video_qc_rejection_target": rejection_target,
        "video_qc_attempts": attempts,
        "video_version": next_video_version
    }


# ==========================================
# 6. Node 6: draft_and_save_copy
# ==========================================
async def draft_and_save_copy_node(state: ContentCreationState):
    """Step 6: Content Creator drafts copy, Brand Editor polishes, and saves to copy_path."""
    if state.get("error_message"):
        return {}
    topic = state.get("topic", "")
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir", ""))
    creator_instructions_path = _resolve_project_doc_path(state.get("creator_instructions_path"), project_dir, "02_Creator_Instructions.md")
    qc_playbook_path = _resolve_project_doc_path(state.get("qc_playbook_path"), project_dir, "03_QC_Playbook.md")
    copy_version = state.get("copy_version") or 1
    copy_path = _resolve_asset_path(state.get("copy_path", ""), output_dir, topic, "copy", copy_version)
    video_path = _resolve_asset_path(state.get("video_path", ""), output_dir, topic, "video", state.get("video_version", 1))
    image_path = _resolve_asset_path(state.get("image_path", ""), output_dir, topic, "image", state.get("image_version", 1))
    video_plot_path = _resolve_asset_path(state.get("video_plot_path", ""), output_dir, topic, "video_plot", state.get("video_plot_version", 1))
    extracted_frames = state.get("extracted_frames", [])
    execution_log_path = os.path.join(output_dir, "execution_log.md") if output_dir else ""
    human_feedback = state.get("latest_human_feedback", "")

    creator = AgentsLoader().get_agent("content-creator")
    editor = AgentsLoader().get_agent("brand-editor")

    creator_prompt = (
        f"You are the Content Creator. Read {creator_instructions_path}.\n"
        f"Draft the publication copy / captions for the topic '{topic}'."
    )
    if human_feedback and state.get("gate2_decision") == "revise_copy":
        creator_prompt += f"\nHuman Revision Notes to apply:\n{human_feedback}\n"

    draft_copy = await creator.execute(creator_prompt, source="subgraph")

    editor_prompt = (
        f"You are the Brand Editor. Read {qc_playbook_path}.\n"
        f"Polish the following copy for formatting, engagement, and alignment with the playbook:\n"
        f"--- Draft Copy ---\n{draft_copy}\n------------------\n"
        f"Output the final polished copy directly."
    )
    polished_copy = await editor.execute(editor_prompt, source="subgraph")
    polished_copy = polished_copy.strip()

    try:
        if copy_path:
            os.makedirs(os.path.dirname(copy_path), exist_ok=True)
            with open(copy_path, "w", encoding="utf-8") as f:
                f.write(polished_copy)
    except Exception as e:
        print(f"ContentCreationGraph: Error saving copy to {copy_path}: {e}")

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
        actor="📱 Brand Editor & Content Creator",
        event_title=f"Publication Copy Drafted & Polished (v{copy_version})",
        details={
            "Copy Path": copy_path,
            "Polished Copy": polished_copy
        },
        log_path=execution_log_path
    )

    return {
        "project_dir": project_dir,
        "output_dir": output_dir,
        "copy_version": copy_version,
        "copy_text": polished_copy,
        "final_package": final_package,
        "copy_path": copy_path
    }