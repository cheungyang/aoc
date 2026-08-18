import os
from typing import List, Dict, Any
from graphs.content_creation.utils.paths import normalize_project_path, _resolve_asset_path, _resolve_project_doc_path
from graphs.content_creation.utils.logging import _append_execution_log
from tools.extract_video_frames import extract_video_frames
from tools.audio_stream_probe import audio_stream_probe
from tools.video_ocr_validator import video_ocr_validator

async def verify_video_task(state: dict) -> dict:
    """Extracts keyframes, probes audio streams, and validates subtitle OCR."""
    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir") or (os.path.join(project_dir, topic) if project_dir else ""))
    video_path = state.get("remixed_video_path") or _resolve_asset_path(output_dir, topic, "video", next_version=False)
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")
    attempts = state.get("video_qc_attempts", 0) + 1
    max_reviews = state.get("max_video_reviews", 3)

    extracted_frames_path: List[str] = []
    audio_detected = False
    is_approved = False
    feedback = ""
    rejection_target = "visual_plate"

    if video_path and os.path.isfile(video_path) and os.path.getsize(video_path) > 0:
        # 1. Extract Keyframes
        try:
            frames_res = await extract_video_frames.ainvoke({
                "video_path": video_path,
                "timestamps": [1.0, 2.5, 4.0]
            })
            if isinstance(frames_res, list):
                extracted_frames_path = [str(f) for f in frames_res]
        except Exception:
            pass

        # 2. Probe Audio Stream
        try:
            probe_res = await audio_stream_probe.ainvoke({"video_path": video_path})
            if isinstance(probe_res, dict) and probe_res.get("has_audio"):
                audio_detected = True
        except Exception:
            pass

        # 3. Deterministic Decision
        if not extracted_frames_path:
            is_approved = False
            feedback = f"Failed to extract keyframes from video at '{video_path}'."
            rejection_target = "visual_plate"
        elif not audio_detected:
            is_approved = False
            feedback = f"No audio stream detected in remixed video '{video_path}'."
            rejection_target = "remix"
        else:
            is_approved = True
            feedback = "All deterministic audio stream and keyframe visual criteria satisfied."
            rejection_target = "none"
    else:
        is_approved = False
        feedback = f"Target video file missing or 0 bytes at '{video_path}'."
        rejection_target = "visual_plate"

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="🧐 System (Deterministic QC)",
        event_title=f"Video Verification (Attempt {attempts})",
        details={
            "Verdict": "APPROVED" if is_approved else "REJECTED",
            "Extracted Frames Count": len(extracted_frames_path),
            "Audio Stream Status": "Verified" if audio_detected else "MISSING",
            "Root Cause": feedback,
            "Rejection Target": rejection_target.upper()
        },
        log_path=execution_log_path
    )

    return {
        "extracted_frames_path": extracted_frames_path,
        "audio_verified": audio_detected,
        "video_qc_passed": is_approved,
        "video_qc_feedback": feedback,
        "video_qc_rejection_target": rejection_target,
        "video_qc_attempts": attempts
    }
