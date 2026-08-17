import os
from graphs.content_creation.utils.paths import normalize_project_path, _resolve_asset_path
from graphs.content_creation.utils.logging import _append_execution_log
from tools.generate_image import generate_image

async def generate_image_task(state: dict) -> dict:
    """Generates 1-shot base image or reuses existing on disk if approved."""
    if state.get("error_message"):
        return {}

    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir") or (os.path.join(project_dir, topic) if project_dir else ""))
    manifest_path = state.get("manifest_path", "")
    creator_instructions_path = state.get("creator_instructions_path", "")
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")
    human_feedback = state.get("latest_human_feedback")

    existing_image = _resolve_asset_path(output_dir, topic, "image", next_version=False)
    needs_image_revision = (
        state.get("gate1_decision") == "revise_image" or
        state.get("qc_rejection_target") == "image" or
        "TARGET: IMAGE" in str(state.get("video_plot_feedback") or "").upper() or
        "BASE IMAGE" in str(state.get("video_plot_feedback") or "").upper()
    )

    if os.path.exists(existing_image) and not needs_image_revision:
        return {
            "project_dir": project_dir,
            "output_dir": output_dir,
            "image_path": existing_image
        }

    if os.path.exists(existing_image) and needs_image_revision:
        image_path = _resolve_asset_path(output_dir, topic, "image", next_version=True)
    else:
        image_path = existing_image

    guidelines = ""
    for path in [manifest_path, creator_instructions_path]:
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    guidelines += f"\n--- {os.path.basename(path)} ---\n" + f.read()
            except Exception:
                pass

    prompt = (
        f"A clear, vibrant, high quality 3D Pixar-style scene featuring '{topic}'. "
        f"The scene must be child-friendly, colorful, and strictly adhere to project character guidelines."
    )
    if guidelines:
        prompt += f"\n\nProject Guidelines Reference:\n{guidelines[:1500]}"
    if human_feedback and state.get("gate1_decision") == "revise_image":
        prompt += f"\n\nHuman Revision Instructions to follow strictly:\n{human_feedback}"

    res = await generate_image.ainvoke({
        "prompt": prompt,
        "output_path": image_path,
        "aspect_ratio": "9:16"
    })

    file_persisted = bool(image_path and os.path.isfile(image_path) and os.path.getsize(image_path) > 0)

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="🎨 Content Creator",
        event_title="Base Image Generation",
        details={
            "Image Path": image_path,
            "File Status": "Verified on disk" if file_persisted else "FAILED / Missing",
            "Tool Response": str(res)
        },
        log_path=execution_log_path
    )

    return {
        "project_dir": project_dir,
        "output_dir": output_dir,
        "image_path": image_path
    }
