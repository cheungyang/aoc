import os
from langchain_core.messages import AIMessage
from graphs.content_creation.utils.paths import normalize_project_path, canonicalize_output_dir, validate_inter_node_paths
from graphs.content_creation.utils.logging import _append_execution_log
from graphs.content_creation.adapters import format_gate2_presentation
from .render_plate import render_plate_task
from .remix_video import remix_video_task
from .verify_video import verify_video_task
from .draft_copy import draft_copy_task

async def produce_deliverables_node(state: dict) -> dict:
    """Macro-Node 3: Generates visual plate, remixes video, runs QC, drafts copy, and formats Gate 2 Card."""
    if state.get("error_message"):
        return {}

    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = canonicalize_output_dir(project_dir, state.get("output_dir"), topic)
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")

    working_state = dict(state)
    working_state["project_dir"] = project_dir
    working_state["output_dir"] = output_dir

    human_feedback = state.get("latest_human_feedback")
    gate2_decision = state.get("gate2_decision")
    if human_feedback and (not gate2_decision or gate2_decision == "approved"):
        from graphs.content_creation.utils.classifiers import classify_gate2_intent
        gate2_decision = classify_gate2_intent(human_feedback)
        working_state["gate2_decision"] = gate2_decision

    # Step 3a: Generate or reuse raw visual plate
    plate_res = await render_plate_task(working_state)
    working_state.update(plate_res)

    if working_state.get("error_message"):
        err_msg = working_state["error_message"]
        _append_execution_log(
            output_dir=output_dir,
            topic=topic,
            actor="🛑 System",
            event_title="Pipeline Halted: Visual Plate Generation Error",
            details={
                "Error": err_msg,
                "Quota Exceeded": working_state.get("quota_exceeded", False)
            },
            log_path=execution_log_path
        )
        return {
            "project_dir": project_dir,
            "output_dir": output_dir,
            "error_message": err_msg,
            "quota_exceeded": working_state.get("quota_exceeded", False),
            "messages": [AIMessage(content=err_msg)]
        }

    # Step 3b: FFmpeg Remixing + Video QC Verification (up to 3 internal attempts)
    max_remix_attempts = 3
    for attempt in range(max_remix_attempts):
        remix_res = await remix_video_task(working_state)
        working_state.update(remix_res)

        verify_res = await verify_video_task(working_state)
        working_state.update(verify_res)

        if working_state.get("video_qc_passed"):
            break

    # Step 3c: Draft and dual-publish publication copy (.md and .json)
    copy_res = await draft_copy_task(working_state)
    working_state.update(copy_res)

    remixed_video_path = working_state.get("remixed_video_path") or working_state.get("video_path", "")
    copy_path = working_state.get("copy_path", "")
    image_path = working_state.get("image_path", "")
    video_plot_path = working_state.get("video_plot_path", "")
    extracted_frames_path = working_state.get("extracted_frames_path", [])

    final_package = {
        "project_dir": project_dir,
        "topic": topic,
        "output_dir": output_dir,
        "image_path": image_path,
        "video_plot_path": video_plot_path,
        "raw_video_path": working_state.get("raw_video_path", ""),
        "remixed_video_path": remixed_video_path,
        "copy_path": copy_path,
        "extracted_frames_path": extracted_frames_path
    }
    working_state["final_package"] = final_package

    # Step 3d: Validate Inter-node Path Invariants and Revision Invariants
    validate_inter_node_paths(working_state, "produce_deliverables")
    from graphs.content_creation.utils.invariants import assert_gate2_revision_invariants
    assert_gate2_revision_invariants(state, working_state)

    # Step 3e: Format Gate 2 Presentation Card
    summary = format_gate2_presentation(working_state)

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="🎉 Human-in-the-Loop",
        event_title="Gate 2 Final Package Presented",
        details={
            "Base Image": image_path,
            "Master Video": remixed_video_path,
            "Copy Path": copy_path,
            "Status": "Awaiting Final Signoff"
        },
        log_path=execution_log_path
    )

    return {
        "project_dir": project_dir,
        "output_dir": output_dir,
        "raw_video_path": working_state.get("raw_video_path", ""),
        "remixed_video_path": remixed_video_path,
        "copy_path": copy_path,
        "extracted_frames_path": extracted_frames_path,
        "video_qc_passed": working_state.get("video_qc_passed", False),
        "video_qc_attempts": working_state.get("video_qc_attempts", 1),
        "video_qc_feedback": working_state.get("video_qc_feedback", ""),
        "gate2_decision": working_state.get("gate2_decision", "approved"),
        "final_package": final_package,
        "messages": [AIMessage(content=summary)]
    }
