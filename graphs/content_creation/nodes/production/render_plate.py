import os
from graphs.content_creation.utils.paths import normalize_path, canonicalize_output_dir, resolve_task_asset, resolve_asset_path, load_project_context
from graphs.content_creation.utils.logging import _append_execution_log
from graphs.content_creation.utils.classifiers import classify_gate2_intent
from tools.generate_animation_veo3 import generate_animation_veo3

async def render_plate_task(state: dict) -> dict:
    """Generates the raw visual plate (Veo 3) from base image and motion plot."""
    if state.get("error_message"):
        return {}

    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_dir = normalize_path(state.get("project_dir", ""))
    output_dir = canonicalize_output_dir(project_dir, state.get("output_dir"), topic)
    image_path = state.get("image_path") or resolve_asset_path(output_dir, topic, "image", next_version=False)
    video_plot_path = state.get("video_plot_path") or resolve_asset_path(output_dir, topic, "video_plot", next_version=False)
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")
    human_feedback = state.get("latest_human_feedback")
    gate2_decision = state.get("gate2_decision")
    if human_feedback and (not gate2_decision or gate2_decision == "approved"):
        gate2_decision = classify_gate2_intent(human_feedback)

    needs_plate_revision = (
        gate2_decision in ["revise_video", "revise_animation"] or
        state.get("video_qc_rejection_target") == "visual_plate" or
        bool(human_feedback and gate2_decision in ["revise_video", "revise_animation"])
    )

    raw_video_path, should_generate = resolve_task_asset(output_dir, topic, "raw_video", needs_revision=needs_plate_revision)
    if not should_generate and state.get("video_qc_passed"):
        return {
            "project_dir": project_dir,
            "output_dir": output_dir,
            "raw_video_path": raw_video_path
        }

    motion_prompt = ""
    if video_plot_path and os.path.exists(video_plot_path):
        try:
            with open(video_plot_path, "r", encoding="utf-8") as f:
                motion_prompt = f.read()
        except Exception:
            pass

    if not motion_prompt:
        motion_prompt = f"3D animation of {topic}, high quality Pixar style."

    # Dynamic aspect ratio from state or project context
    aspect_ratio = state.get("aspect_ratio")
    if not aspect_ratio:
        ctx = load_project_context(
            project_dir=project_dir,
            manifest_path=state.get("manifest_path", ""),
            creator_instructions_path=state.get("creator_instructions_path", "")
        )
        aspect_ratio = ctx["aspect_ratio"]
    aspect_ratio = aspect_ratio or "16:9"

    try:
        result = await generate_animation_veo3.ainvoke({
            "prompt_text": motion_prompt,
            "image_path": image_path,
            "output_path": raw_video_path,
            "duration": 6,
            "aspect_ratio": aspect_ratio,
            "agent_id": "content-creator"
        })
        if "<payload>" in result and "</payload>" in result:
            saved = result.split("<payload>")[1].split("</payload>")[0].strip()
            if saved and os.path.exists(saved):
                if raw_video_path and os.path.abspath(saved) == os.path.abspath(raw_video_path):
                    pass
                else:
                    from core.util.config import Config
                    codebase_dir = Config().codebase_dir
                    if saved.startswith(codebase_dir):
                        raw_video_path = os.path.relpath(saved, codebase_dir)
                    else:
                        raw_video_path = saved
        tool_err = ""
        if "<errors>" in result and "</errors>" in result:
            tool_err = result.split("<errors>")[1].split("</errors>")[0].strip()
            if tool_err and tool_err.lower() != "none":
                print(f"render_plate_task: generate_animation_veo3 error: {tool_err}")
    except Exception as e:
        tool_err = str(e)
        print(f"render_plate_task: Error generating video: {e}")
        return {"error_message": f"Veo 3 API Error: {e}", "failed_node": "generate_visual_plate"}

    file_persisted = bool(raw_video_path and os.path.isfile(raw_video_path) and os.path.getsize(raw_video_path) > 0)

    log_details = {
        "Raw Video Path": raw_video_path,
        "Source Image": image_path,
        "Aspect Ratio": aspect_ratio,
        "File Status": "Verified on disk" if file_persisted else "FAILED / Missing"
    }
    if not file_persisted and tool_err and tool_err.lower() != "none":
        log_details["Error Details"] = tool_err

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="🎬 Content Creator",
        event_title="Visual Plate Generation",
        details=log_details,
        log_path=execution_log_path
    )

    return {
        "project_dir": project_dir,
        "output_dir": output_dir,
        "raw_video_path": raw_video_path
    }
