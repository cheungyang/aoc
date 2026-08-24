import os
import re
from graphs.content_creation.utils.paths import resolve_task_asset, load_project_context
from graphs.content_creation.utils.logging import _append_execution_log
from graphs.content_creation.utils.classifiers import classify_gate1_intent
from tools.generate_image import generate_image

async def generate_image_task(state: dict) -> dict:
    """Generates 1-shot base image using instructions and character sheets loaded dynamically from project_path."""
    if state.get("error_message"):
        return {}

    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    style = str(state.get("style") or "3D").strip()
    project_path = state.get("project_path", "")
    output_path = state.get("output_path", "")
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_path, "execution_log.md") if output_path else "")
    human_feedback = state.get("latest_human_feedback")
    gate1_decision = state.get("gate1_decision")
    if human_feedback and (not gate1_decision or gate1_decision == "approved"):
        gate1_decision = classify_gate1_intent(human_feedback)

    needs_image_revision = (
        gate1_decision == "revise_image" or
        state.get("qc_rejection_target") == "image" or
        "TARGET: IMAGE" in str(state.get("video_plot_feedback") or "").upper() or
        "BASE IMAGE" in str(state.get("video_plot_feedback") or "").upper() or
        bool(human_feedback and gate1_decision not in ["approved", "revise_plot"])
    )

    image_path, should_generate = resolve_task_asset(output_path, topic, "image", needs_revision=needs_image_revision)
    if not should_generate:
        return {
            "project_path": project_path,
            "output_path": output_path,
            "image_path": image_path
        }

    # Load context, guidelines, character sheet, and aspect ratio
    ctx = load_project_context(
        project_path=project_path,
        style=style,
        manifest_path=state.get("manifest_path", ""),
        creator_instructions_path=state.get("creator_instructions_path", "")
    )
    style_normalized = ctx["style_normalized"]
    aspect_ratio = state.get("aspect_ratio") or ctx["aspect_ratio"]
    ref_image_path = state.get("reference_image_path") or ctx["ref_image_path"]

    # Check if human feedback explicitly references a specific image file
    if human_feedback:
        m_custom_ref = re.search(r'(?:reference|ref|character)[^\w\n]*([\w\d_./-]+\.(?:jpg|jpeg|png|webp))', human_feedback, re.IGNORECASE)
        if m_custom_ref:
            custom_ref_file = m_custom_ref.group(1).strip()
            char_dir = os.path.join(project_path, "character") if project_path else ""
            cands = [
                os.path.join(char_dir, os.path.basename(custom_ref_file)) if char_dir else "",
                os.path.join(project_path, custom_ref_file) if project_path else "",
                custom_ref_file
            ]
            for cand in cands:
                if cand and os.path.isfile(cand):
                    ref_image_path = cand
                    break

    prompt_sections = []
    if ctx["char_guidelines"]:
        prompt_sections.append(f"--- CHARACTER IDENTITY & APPEARANCE RULES ({style_normalized} Style) ---\n{ctx['char_guidelines']}")
    if ctx["project_guidelines"]:
        prompt_sections.append(f"--- PROJECT CREATIVE & CREATOR INSTRUCTIONS ---\n{ctx['project_guidelines']}")
    prompt_sections.append(
        f"TASK: Generate the 1-shot base image for topic: '{topic}', style: '{style_normalized}'.\n"
        f"Strictly adhere to the character appearance rules, costume requirements, and scene composition instructions defined above."
    )
    if human_feedback:
        prompt_sections.append(f"--- HUMAN REVISION INSTRUCTIONS (HIGHEST PRIORITY) ---\n{human_feedback}")

    tool_args = {"prompt": "\n\n".join(prompt_sections), "output_path": image_path}
    if ref_image_path and os.path.isfile(ref_image_path):
        tool_args["image_path"] = ref_image_path

    res = await generate_image.ainvoke(tool_args)
    tool_err = ""
    if "<errors>" in str(res) and "</errors>" in str(res):
        tool_err = str(res).split("<errors>")[1].split("</errors>")[0].strip()

    file_persisted = bool(image_path and os.path.isfile(image_path) and os.path.getsize(image_path) > 0)

    log_details = {
        "Style": style_normalized,
        "Aspect Ratio": aspect_ratio,
        "Image Path": image_path,
        "Reference Image Used": ref_image_path or "None",
        "File Status": "Verified on disk" if file_persisted else "FAILED / Missing",
        "Tool Response": str(res)
    }
    if not file_persisted and tool_err and tool_err.lower() != "none":
        log_details["Error Details"] = tool_err

    _append_execution_log(
        output_path=output_path,
        topic=topic,
        actor="🎨 Content Creator",
        event_title="Base Image Generation",
        details=log_details,
        log_path=execution_log_path
    )

    if not file_persisted:
        from graphs.content_creation.utils.errors import is_quota_exceeded_error, format_quota_exceeded_message
        if is_quota_exceeded_error(tool_err):
            err_msg = format_quota_exceeded_message("Google Imagen / Gemini", tool_err or "API Quota Exceeded (429)", topic)
            return {
                "project_path": project_path,
                "output_path": output_path,
                "error_message": err_msg,
                "quota_exceeded": True,
                "failed_node": "generate_image"
            }
        elif tool_err and tool_err.lower() != "none":
            return {
                "project_path": project_path,
                "output_path": output_path,
                "error_message": f"Image Generation Failed: {tool_err}",
                "failed_node": "generate_image"
            }

    return {
        "project_path": project_path,
        "output_path": output_path,
        "aspect_ratio": aspect_ratio,
        "image_path": image_path
    }
