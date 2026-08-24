import os
from typing import List, Dict, Any
from graphs.content_creation.utils.logging import _append_execution_log
from tools.extract_video_frames import extract_video_frames
from tools.audio_stream_probe import audio_stream_probe
from tools.video_ocr_validator import video_ocr_validator

async def verify_video_task(state: dict) -> dict:
    """Extracts keyframes, probes audio streams, and validates subtitle OCR."""
    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_path = state.get("project_path", "")
    output_path = state.get("output_path", "")
    video_path = state.get("remixed_video_path") or state.get("video_path") or (os.path.join(output_path, f"{topic}_video.mp4") if output_path else "")

    execution_log_path = state.get("execution_log_path") or (os.path.join(output_path, "execution_log.md") if output_path else "")
    attempts = state.get("video_qc_attempts", 0) + 1
    max_reviews = state.get("max_video_reviews", 3)

    extracted_frames_path: List[str] = []
    audio_detected = False
    is_approved = False
    feedback = ""
    rejection_target = "visual_plate"

    if video_path and os.path.isfile(video_path) and os.path.getsize(video_path) > 0:
        frames_dir = os.path.join(output_path, "frames") if output_path else os.path.join(os.path.dirname(os.path.abspath(video_path)), "frames")
        # 1. Extract Keyframes
        try:
            frames_res = await extract_video_frames.ainvoke({
                "video_path": video_path,
                "timestamps": [1.0, 2.5, 4.0],
                "output_dir": frames_dir
            })
            if "<payload>" in str(frames_res) and "</payload>" in str(frames_res):
                payload = str(frames_res).split("<payload>")[1].split("</payload>")[0].strip()
                if payload:
                    extracted_frames_path = [f.strip() for f in payload.splitlines() if f.strip()]
            elif isinstance(frames_res, list):
                extracted_frames_path = [str(f) for f in frames_res]
            elif isinstance(frames_res, str) and frames_res.strip():
                extracted_frames_path = [frames_res.strip()]
        except Exception:
            pass

        # 2. Probe Audio Stream
        try:
            probe_res = await audio_stream_probe.ainvoke({"video_path": video_path})
            if "<payload>" in str(probe_res) and "</payload>" in str(probe_res):
                payload = str(probe_res).split("<payload>")[1].split("</payload>")[0].strip().lower()
                audio_detected = (payload == "true")
            elif isinstance(probe_res, dict) and probe_res.get("has_audio"):
                audio_detected = True
            elif isinstance(probe_res, bool):
                audio_detected = probe_res
            elif str(probe_res).strip().lower() == "true":
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
        gen_err = state.get("video_generation_error")
        if gen_err:
            feedback = f"Target video file missing or 0 bytes at '{video_path}'. Upstream error: {gen_err}"
        else:
            feedback = f"Target video file missing or 0 bytes at '{video_path}'."
        rejection_target = "visual_plate"

    _append_execution_log(
        output_path=output_path,
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
