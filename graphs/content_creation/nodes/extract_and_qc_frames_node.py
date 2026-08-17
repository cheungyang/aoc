import os
import json
from typing import Dict, Any, List
from datetime import datetime, timezone
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field

from graphs.content_creation.utils.paths import normalize_project_path, _resolve_asset_path, _resolve_project_doc_path
from graphs.content_creation.utils.logging import _append_execution_log

from tools.extract_video_frames import extract_video_frames
from tools.audio_stream_probe import audio_stream_probe


async def extract_and_qc_frames_node(state: dict):
    """Step 6: Brand Editor checks audio stream, extracts video frames, and audits text overlay & visual fidelity."""
    if state.get("error_message"):
        return {}
    topic = state.get("topic", "")
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir", ""))
    video_path = _resolve_asset_path(output_dir, topic, "video", next_version=False)
    qc_playbook_path = _resolve_project_doc_path(state.get("qc_playbook_path"), project_dir, "03_QC_Playbook.md")
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")
    qc_timestamps = state.get("qc_timestamps") or [1.0, 2.0, 2.5, 3.5, 4.0]
    frames_dir = os.path.join(output_dir, "frames") if output_dir else "frames"
    attempts = state.get("video_qc_attempts", 0) + 1
    max_reviews = state.get("max_video_reviews", 3)

    # 1. Fast-Fail Diagnosis: Check if video exists and is non-empty on disk
    file_persisted = bool(video_path and os.path.isfile(video_path) and os.path.getsize(video_path) > 0)
    raw_video_path = _resolve_asset_path(output_dir, topic, "raw_video", next_version=False)
    raw_persisted = bool(raw_video_path and os.path.isfile(raw_video_path) and os.path.getsize(raw_video_path) > 0)

    if not file_persisted:
        gen_err = state.get("video_generation_error") or "Remixed video file not found or 0 bytes on disk."
        root_cause = f"Video file missing or empty on disk at '{video_path}'. ({gen_err})"
        rejection_target: Literal["visual_plate", "remix", "both"] = "remix" if raw_persisted else "visual_plate"
        action_taken = (
            f"Re-routed to Remix Video for audio/text re-assembly."
            if (rejection_target == "remix" and attempts < max_reviews) else (
                f"Re-routed to Content Creator for fresh visual plate render."
                if attempts < max_reviews else
                "Max retry attempts reached. Hard blocking at Fail-Fast node."
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
            "extracted_frames": [],
            "video_qc_passed": False,
            "video_qc_feedback": root_cause,
            "video_qc_rejection_target": rejection_target,
            "video_qc_attempts": attempts
        }

    # 2. Automated Audio Stream Integrity Verification
    audio_detected = False
    try:
        audio_res = await audio_stream_probe.ainvoke({"video_path": video_path})
        if isinstance(audio_res, dict):
            audio_detected = bool(audio_res.get("payload") is True or "True" in str(audio_res.get("payload")))
        elif isinstance(audio_res, str):
            audio_detected = "<payload>True</payload>" in audio_res or "True" in audio_res
    except Exception as e:
        print(f"ContentCreationGraph: Error probing audio stream: {e}")

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
        return {"error_message": f"FFmpeg Frame Extraction Error: {e}", "failed_node": "extract_and_qc_frames"}

    if not extracted_frames and os.path.exists(frames_dir):
        extracted_frames = [
            os.path.join(frames_dir, f)
            for f in sorted(os.listdir(frames_dir))
            if f.endswith((".jpg", ".png", ".jpeg"))
        ]

    return {
        "extracted_frames": extracted_frames,
        "audio_verified": audio_detected
    }
