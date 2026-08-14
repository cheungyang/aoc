import os
from typing import Dict, Any, Literal
from core.loaders.agents_loader import AgentsLoader
from tools.generate_image import generate_image
from tools.generate_animation_runway import generate_animation_runway
from tools.extract_video_frames import extract_video_frames
from graphs.content_creation.state import (
    ContentCreationState,
    _resolve_asset_path,
    _append_execution_log,
    _extract_motion_prompt_from_plot
)


# ==========================================
# 1. Node 1: setup_and_generate_image
# ==========================================
async def setup_and_generate_image_node(state: ContentCreationState):
    """Step 1: Content Creator reads project manifest and creator instructions to generate 1-shot base image."""
    project_dir = state.get("project_dir") or "pkm/wiki/software/ayla-first-words"
    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    output_dir = state.get("output_dir") or f"{project_dir}/words/{topic}"
    manifest_path = state.get("manifest_path") or f"{project_dir}/01_Project_Manifest.md"
    creator_instructions_path = state.get("creator_instructions_path") or f"{project_dir}/02_Creator_Instructions.md"
    qc_playbook_path = state.get("qc_playbook_path") or f"{project_dir}/03_QC_Playbook.md"
    execution_log_path = state.get("execution_log_path") or f"{output_dir}/execution_log.md"

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
    topic = state.get("topic", "scene")
    creator_instructions_path = state.get("creator_instructions_path", "")
    output_dir = state.get("output_dir", "")
    video_plot_version = state.get("video_plot_version") or 1
    video_plot_path = _resolve_asset_path(state.get("video_plot_path", ""), output_dir, topic, "video_plot", video_plot_version)
    execution_log_path = state.get("execution_log_path")
    feedback = state.get("video_plot_feedback", "")
    human_feedback = state.get("latest_human_feedback", "")

    creator = AgentsLoader().get_agent("content-creator")
    prompt = (
        f"You are the Content Creator. Read {creator_instructions_path}.\n"
        f"Draft the Video Plot Markdown for the topic '{topic}' strictly following the template and constraints defined in the instructions."
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
    topic = state.get("topic", "")
    qc_playbook_path = state.get("qc_playbook_path", "")
    image_path = state.get("image_path", "")
    image_prompt = state.get("image_prompt", "")
    plot_content = state.get("video_plot_content", "")
    video_plot_path = state.get("video_plot_path", "")
    output_dir = state.get("output_dir", "")
    execution_log_path = state.get("execution_log_path")
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
    """Step 4: Content Creator uses approved motion prompt to generate video visual plate."""
    image_path = state.get("image_path", "")
    topic = state.get("topic", "")
    output_dir = state.get("output_dir", "")
    video_version = state.get("video_version") or 1
    video_path = _resolve_asset_path(state.get("video_path", ""), output_dir, topic, "video", video_version)
    plot_content = state.get("video_plot_content", "")
    execution_log_path = state.get("execution_log_path")

    motion_prompt = _extract_motion_prompt_from_plot(plot_content, state)
    print(f"ContentCreationGraph: Generating visual plate (v{video_version}) from {image_path} to {video_path}...")

    try:
        result = await generate_animation_runway.ainvoke({
            "prompt_text": motion_prompt,
            "image_path": image_path,
            "output_path": video_path,
            "ratio": "768:1280",  # 9:16 vertical
            "duration": 5,
            "agent_id": "content-creator"
        })
        if "<payload>" in result and "</payload>" in result:
            saved = result.split("<payload>")[1].split("</payload>")[0].strip()
            if saved:
                video_path = saved
    except Exception as e:
        print(f"ContentCreationGraph: Error generating visual plate: {e}")

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="🎬 Content Creator",
        event_title=f"Visual Plate Video Generation (v{video_version})",
        details={
            "Video Output Path": video_path,
            "Motion Prompt": motion_prompt
        },
        log_path=execution_log_path
    )

    return {
        "video_version": video_version,
        "video_path": video_path
    }


# ==========================================
# 5. Node 5: extract_and_qc_frames
# ==========================================
async def extract_and_qc_frames_node(state: ContentCreationState):
    """Step 5: Brand Editor extracts video frames and performs QC based on QC playbook."""
    topic = state.get("topic", "")
    output_dir = state.get("output_dir", "")
    video_path = state.get("video_path", "")
    qc_playbook_path = state.get("qc_playbook_path", "")
    execution_log_path = state.get("execution_log_path")
    qc_timestamps = state.get("qc_timestamps") or [1.0, 2.5, 4.0]
    frames_dir = os.path.join(output_dir, "frames")
    attempts = state.get("video_qc_attempts", 0) + 1
    video_version = state.get("video_version", 1)

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

    editor = AgentsLoader().get_agent("brand-editor")
    frames_list_str = "\n".join([f"- Frame {i+1} ({qc_timestamps[i] if i < len(qc_timestamps) else ''}s): {f}" for i, f in enumerate(extracted_frames)])

    prompt = (
        f"You are the Brand Editor. Read {qc_playbook_path}.\n"
        f"Audit the extracted keyframes for the topic '{topic}'.\n"
        f"Video Path: {video_path}\n"
        f"Extracted Frames:\n{frames_list_str}\n\n"
        f"Reply VERDICT: APPROVED or VERDICT: REJECTED based strictly on the playbook criteria."
    )

    response = await editor.execute(prompt, source="subgraph")
    is_approved = "VERDICT: APPROVED" in response.upper() or ("APPROVED" in response.upper() and "REJECTED" not in response.upper())
    feedback = "" if is_approved else response

    if not is_approved and attempts < state.get("max_video_reviews", 3):
        video_version += 1

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="🧐 Brand Editor",
        event_title=f"Video Keyframe QC Audit (Attempt {attempts})",
        details={
            "Verdict": "APPROVED" if is_approved else "REJECTED",
            "Extracted Frames Count": len(extracted_frames),
            "Audit Notes": response.strip(),
            "Next Video Version": f"v{video_version}" if not is_approved else f"v{state.get('video_version', 1)}"
        },
        log_path=execution_log_path
    )

    return {
        "extracted_frames": extracted_frames,
        "video_qc_passed": is_approved,
        "video_qc_feedback": feedback,
        "video_qc_attempts": attempts,
        "video_version": video_version
    }


# ==========================================
# 6. Node 6: draft_and_save_copy
# ==========================================
async def draft_and_save_copy_node(state: ContentCreationState):
    """Step 6: Content Creator drafts copy, Brand Editor polishes, and saves to copy_path."""
    topic = state.get("topic", "")
    creator_instructions_path = state.get("creator_instructions_path", "")
    qc_playbook_path = state.get("qc_playbook_path", "")
    output_dir = state.get("output_dir", "")
    copy_version = state.get("copy_version") or 1
    copy_path = _resolve_asset_path(state.get("copy_path", ""), output_dir, topic, "copy", copy_version)
    video_path = state.get("video_path", "")
    image_path = state.get("image_path", "")
    video_plot_path = state.get("video_plot_path", "")
    extracted_frames = state.get("extracted_frames", [])
    execution_log_path = state.get("execution_log_path")
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
        "project_dir": state.get("project_dir", ""),
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
        "copy_version": copy_version,
        "copy_text": polished_copy,
        "final_package": final_package,
        "copy_path": copy_path
    }
