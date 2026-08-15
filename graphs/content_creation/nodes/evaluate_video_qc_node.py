import os
import json
from typing import Dict, Any, List
from datetime import datetime, timezone

from graphs.content_creation.utils.paths import normalize_project_path, _resolve_asset_path, _resolve_project_doc_path
from graphs.content_creation.utils.logging import _append_execution_log

async def evaluate_video_qc_node(state: dict):
    """Step 6b: System evaluates video text OCR, audio, and visual fidelity deterministically."""
    if state.get("error_message"):
        return {}
        
    topic = state.get("topic", "")
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir", ""))
    video_path = _resolve_asset_path(output_dir, topic, "video", next_version=False)
    qc_playbook_path = _resolve_project_doc_path(state.get("qc_playbook_path"), project_dir, "03_QC_Playbook.md")
    execution_log_path = os.path.join(output_dir, "execution_log.md") if output_dir else ""
    attempts = state.get("video_qc_attempts", 0) + 1  # Note: Attempts bump happens here
    max_reviews = state.get("max_video_reviews", 3)
    
    extracted_frames = state.get("extracted_frames", [])
    audio_detected = state.get("audio_verified", False)
    audio_status_str = "Audio stream verified present in remixed video file." if audio_detected else "WARNING: No audio stream detected in remixed video file."

    if not extracted_frames:
        root_cause = f"Failed to extract keyframes from video at '{video_path}'."
        action_taken = (
            f"Re-routed to Content Creator for fresh visual plate render."
            if attempts < max_reviews else
            "Max retry attempts reached. Hard blocking at Fail-Fast node."
        )

        _append_execution_log(
            output_dir=output_dir,
            topic=topic,
            action="🧐 System (Deterministic)",
            event_title=f"Video Dual Text/Audio Keyframe QC Audit (Attempt {attempts})",
            details={
                "Verdict": "REJECTED",
                "Extracted Frames Count": 0,
                "Audio Stream Status": audio_status_str,
                "Rejection Target": "VISUAL_PLATE",
                "Specific Root Cause": root_cause,
                "Action Taken": action_taken
            },
            log_path=execution_log_path
        )
        return {
            "video_qc_passed": False,
            "video_qc_feedback": root_cause,
            "video_qc_rejection_target": "visual_plate",
            "video_qc_attempts": attempts
        }

    # Deterministic OCR check
    ocr_matches_str = "OCR Validation: Not performed."
    all_found = True
    overlay_text = state.get("overlay_text", "")
    
    if overlay_text:
        try:
            ocr_res = await video_ocr_validator.ainvoke({
                "frame_paths": extracted_frames,
                "expected_text": overlay_text
            })
            if "<payload>" in ocr_res:
                payload = ocr_res.split("<payload>")[1].split("</payload>")[0].strip()
                try:
                    ocr_data = json.loads(payload)
                    all_found = ocr_data.get("all_expected_text_found", False)
                    ocr_matches_str = f"OCR Validation: {'PASSED' if all_found else 'FAILED'} (Expected text: {overlay_text})"
                except json.JSONDecodeError:
                    pass
        except Exception as e:
            ocr_matches_str = f"OCR Validation: Error running OCR - {str(e)}"
            all_found = False

    # 4. Deterministic QC Audit on Text Visibility and Audio Status (LLM dropped for deterministic lean execution)
    is_approved = False
    rejection_target = "visual_plate"
    
    # If audio is missing, fail on remix
    if not audio_detected:
        rejection_target = "remix"
        feedback = f"QC Rejection: No audio stream detected in remixed video '{video_path}'."
    # If text OCR fails, fail on remix
    elif not all_found:
        rejection_target = "remix"
        feedback = f"QC Rejection: Text overlay not fully visible in keyframes. Expected: {overlay_text}"
    else:
        # Both deterministic checks passed!
        is_approved = True
        rejection_target = "none"
        feedback = ""

    if is_approved:
        action_taken = "Proceeding to publication copywriting (parallel) and final package assembly."
    elif attempts >= max_reviews:
        action_taken = "Max retry attempts reached. Hard blocking at Fail-Fast node."
    elif rejection_target == "remix":
        action_taken = f"Re-routed to Remix Video for audio/text re-assembly."
    else:
        action_taken = f"Re-routed to Content Creator for fresh visual plate render."

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        action="🧐 System (Deterministic)",
        event_title=f"Video Dual Text/Audio Keyframe QC Audit (Attempt {attempts})",
        details={
            "Verdict": "APPROVED" if is_approved else "REJECTED",
            "Extracted Frames Count": len(extracted_frames),
            "Audio Stream Status": audio_status_str,
            "OCR Status": ocr_matches_str,
            "Rejection Target": rejection_target.upper() if not is_approved else "NONE",
            "Specific Root Cause": feedback if not is_approved else "All deterministic visual overlay and audio criteria satisfied. Deferred motion to human.",
            "Action Taken": action_taken
        },
        log_path=execution_log_path
    )

    return {
        "video_qc_passed": is_approved,
        "video_qc_feedback": feedback,
        "video_qc_rejection_target": rejection_target,
        "video_qc_attempts": attempts
    }
