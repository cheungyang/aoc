import os
from graphs.content_creation.utils.paths import normalize_project_path, _resolve_asset_path
from graphs.content_creation.utils.logging import _append_execution_log
from tools.generate_animation_veo3 import generate_animation_veo3

async def render_plate_task(state: dict) -> dict:
    """Generates the raw visual plate (Veo 3) from base image and motion plot."""
    if state.get("error_message"):
        return {}

    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir") or (os.path.join(project_dir, topic) if project_dir else ""))
    image_path = state.get("image_path") or _resolve_asset_path(output_dir, topic, "image", next_version=False)
    video_plot_path = state.get("video_plot_path") or _resolve_asset_path(output_dir, topic, "video_plot", next_version=False)
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")

    existing_plate = _resolve_asset_path(output_dir, topic, "raw_video", next_version=False)
    needs_plate_revision = (
        state.get("gate2_decision") == "revise_video" or
        state.get("video_qc_rejection_target") == "visual_plate"
    )

    if os.path.exists(existing_plate) and not needs_plate_revision and state.get("video_qc_passed"):
        return {
            "project_dir": project_dir,
            "output_dir": output_dir,
            "raw_video_path": existing_plate
        }

    if os.path.exists(existing_plate) and needs_plate_revision:
        raw_video_path = _resolve_asset_path(output_dir, topic, "raw_video", next_version=True)
    else:
        raw_video_path = existing_plate

    motion_prompt = ""
    if video_plot_path and os.path.exists(video_plot_path):
        try:
            with open(video_plot_path, "r", encoding="utf-8") as f:
                motion_prompt = f.read()
        except Exception:
            pass

    if not motion_prompt:
        motion_prompt = f"3D animation of {topic}, high quality Pixar style."

    gen_error = ""
    try:
        result = await generate_animation_veo3.ainvoke({
            "prompt": motion_prompt,
            "image_path": image_path,
            "output_path": raw_video_path,
            "duration": 6,
            "agent_id": "content-creator"
        })
        if "<errors>" in result and "</errors>" in result:
            err_val = result.split("<errors>")[1].split("</errors>")[0].strip()
            if err_val and err_val.lower() != "none":
                gen_error = err_val
        if "<payload>" in result and "</payload>" in result:
            saved = result.split("<payload>")[1].split("</payload>")[0].strip()
            if saved:
                from core.util.config import Config
                codebase_dir = Config().codebase_dir
                if saved.startswith(codebase_dir):
                    raw_video_path = os.path.relpath(saved, codebase_dir)
                else:
                    raw_video_path = saved
    except Exception as e:
        print(f"render_plate_task: Error generating video: {e}")
        return {"error_message": f"Veo 3 API Error: {e}", "failed_node": "generate_visual_plate"}

    file_persisted = bool(raw_video_path and os.path.isfile(raw_video_path) and os.path.getsize(raw_video_path) > 0)

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="🎬 Content Creator",
        event_title="Visual Plate Generation",
        details={
            "Raw Video Path": raw_video_path,
            "Source Image": image_path,
            "File Status": "Verified on disk" if file_persisted else "FAILED / Missing"
        },
        log_path=execution_log_path
    )

    return {
        "project_dir": project_dir,
        "output_dir": output_dir,
        "raw_video_path": raw_video_path
    }
