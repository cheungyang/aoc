import os
import re
import json
from graphs.content_creation.utils.paths import resolve_task_asset, load_project_context
from graphs.content_creation.utils.logging import _append_execution_log
from graphs.content_creation.utils.classifiers import classify_gate2_intent
from tools.generate_animation_veo3 import generate_animation_veo3

async def render_plate_task(state: dict) -> dict:
    """Generates the raw visual plate (Veo 3) from base image and motion plot."""
    if state.get("error_message"):
        return {}

    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_path = state.get("project_path", "")
    output_path = state.get("output_path", "")
    image_path = state.get("image_path") or (os.path.join(output_path, f"{topic}_image.jpg") if output_path else "")
    video_plot_path = state.get("video_plot_path") or (os.path.join(output_path, f"{topic}_video_plot.md") if output_path else "")
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_path, "execution_log.md") if output_path else "")
    human_feedback = state.get("latest_human_feedback")
    gate2_decision = state.get("gate2_decision")
    if human_feedback and (not gate2_decision or gate2_decision == "approved"):
        gate2_decision = classify_gate2_intent(human_feedback)

    needs_plate_revision = (
        gate2_decision in ["revise_video", "revise_animation"] or
        state.get("video_qc_rejection_target") == "visual_plate" or
        bool(human_feedback and gate2_decision in ["revise_video", "revise_animation"])
    )

    raw_video_path, should_generate = resolve_task_asset(output_path, topic, "raw_video", needs_revision=needs_plate_revision)
    if not should_generate and state.get("video_qc_passed"):
        return {
            "project_path": project_path,
            "output_path": output_path,
            "raw_video_path": raw_video_path
        }

    motion_prompt = ""
    json_path = video_plot_path.replace(".md", ".json") if video_plot_path else ""
    if json_path and os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                motion_prompt = data.get("motion_prompt", "")
        except Exception:
            pass

    if not motion_prompt and video_plot_path and os.path.exists(video_plot_path):
        try:
            with open(video_plot_path, "r", encoding="utf-8") as f:
                raw_plot = f.read()

            # 1. Extract blockquote prompt if present (> **Prompt:** ...)
            m = re.search(r">\s*\*\*Prompt:\*\*\s*(.+?)(?:\n\n|\n---|\Z)", raw_plot, re.DOTALL | re.IGNORECASE)
            if m:
                motion_prompt = m.group(1).strip()
            else:
                # 2. Extract section under Google Veo 3 Motion Prompt
                m2 = re.search(r"##\s*🎬\s*Google Veo 3 Motion Prompt[^\n]*\n+(.+?)(?:\n---|\Z)", raw_plot, re.DOTALL | re.IGNORECASE)
                if m2:
                    motion_prompt = m2.group(1).strip().lstrip(">").strip()
                else:
                    # 3. Clean markdown fences
                    clean_text = raw_plot.replace("```markdown", "").replace("```", "").strip()
                    motion_prompt = clean_text
        except Exception:
            pass

    if not motion_prompt:
        motion_prompt = f"3D animation of {topic}, high quality Pixar style."

    # Dynamic aspect ratio from state or project context
    aspect_ratio = state.get("aspect_ratio")
    if not aspect_ratio:
        ctx = load_project_context(
            project_path=project_path,
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
            "agent_id": "graph-worker"
        })
        if "<payload>" in result and "</payload>" in result:
            saved = result.split("<payload>")[1].split("</payload>")[0].strip()
            if saved and os.path.exists(saved):
                raw_video_path = saved
        tool_err = ""
        if "<errors>" in result and "</errors>" in result:
            tool_err = result.split("<errors>")[1].split("</errors>")[0].strip()
            if tool_err and tool_err.lower() != "none":
                print(f"render_plate_task: generate_animation_veo3 error: {tool_err}")
    except Exception as e:
        tool_err = str(e)
        print(f"render_plate_task: Error generating video: {e}")

    file_persisted = bool(raw_video_path and os.path.isfile(raw_video_path) and os.path.getsize(raw_video_path) > 0)

    from graphs.content_creation.utils.errors import is_quota_exceeded_error, format_quota_exceeded_message

    log_details = {
        "Raw Video Path": raw_video_path,
        "Source Image": image_path,
        "Aspect Ratio": aspect_ratio,
        "File Status": "Verified on disk" if file_persisted else "FAILED / Missing"
    }
    if not file_persisted and tool_err and tool_err.lower() != "none":
        log_details["Error Details"] = tool_err

    _append_execution_log(
        output_path=output_path,
        topic=topic,
        actor="🎬 Content Creator",
        event_title="Visual Plate Generation",
        details=log_details,
        log_path=execution_log_path
    )

    if not file_persisted:
        if is_quota_exceeded_error(tool_err):
            err_msg = format_quota_exceeded_message("Google Veo 3", tool_err or "API Quota Exceeded (429)", topic)
            return {
                "project_path": project_path,
                "output_path": output_path,
                "error_message": err_msg,
                "quota_exceeded": True,
                "failed_node": "generate_visual_plate"
            }
        elif tool_err and tool_err.lower() != "none":
            return {
                "project_path": project_path,
                "output_path": output_path,
                "error_message": f"Veo 3 Visual Plate Generation Failed: {tool_err}",
                "failed_node": "generate_visual_plate"
            }

    return {
        "project_path": project_path,
        "output_path": output_path,
        "raw_video_path": raw_video_path
    }
