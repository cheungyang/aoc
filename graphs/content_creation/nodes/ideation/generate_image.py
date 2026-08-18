import os
import glob
from graphs.content_creation.utils.paths import normalize_project_path, _resolve_asset_path
from graphs.content_creation.utils.logging import _append_execution_log
from tools.generate_image import generate_image

async def generate_image_task(state: dict) -> dict:
    """Generates 1-shot base image using the style-specific character sheet (01_Character_Sheet_{style}.md) and guidelines."""
    if state.get("error_message"):
        return {}

    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    style = str(state.get("style") or "3D").strip()
    style_normalized = style.upper() if style.lower() == "3d" else style.capitalize()

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

    # 1. Discover Style-Specific Character Sheet and Reference Image
    char_dir = os.path.join(project_dir, "character") if project_dir else ""
    ref_image_path = None
    char_guidelines = ""

    if char_dir and os.path.isdir(char_dir):
        # Look specifically for 01_Character_Sheet_{style}.md
        target_sheet_name = f"01_Character_Sheet_{style_normalized}.md"
        target_sheet_path = os.path.join(char_dir, target_sheet_name)
        
        # Check case-insensitive if exact match not found
        if not os.path.exists(target_sheet_path):
            for fname in os.listdir(char_dir):
                if fname.lower() == target_sheet_name.lower() or fname.lower() == f"character_sheet_{style.lower()}.md":
                    target_sheet_path = os.path.join(char_dir, fname)
                    break

        if os.path.exists(target_sheet_path):
            try:
                with open(target_sheet_path, "r", encoding="utf-8") as f:
                    char_guidelines = f"\n--- {os.path.basename(target_sheet_path)} ---\n" + f.read()
            except Exception:
                pass

        # Look for matching style reference image or real reference photo
        image_candidates = glob.glob(os.path.join(char_dir, "*.jpg")) + \
                           glob.glob(os.path.join(char_dir, "*.jpeg")) + \
                           glob.glob(os.path.join(char_dir, "*.png"))
        if image_candidates:
            style_matches = [p for p in image_candidates if style.lower() in os.path.basename(p).lower()]
            real_matches = [p for p in image_candidates if "real" in os.path.basename(p).lower() or "photo" in os.path.basename(p).lower()]
            
            if style_matches:
                ref_image_path = style_matches[0]
            elif real_matches:
                ref_image_path = real_matches[0]
            else:
                ref_image_path = image_candidates[0]

    # 2. Collect Project Guidelines
    project_guidelines = ""
    for path in [manifest_path, creator_instructions_path]:
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    project_guidelines += f"\n--- {os.path.basename(path)} ---\n" + f.read()
            except Exception:
                pass

    # 3. Assemble Style-Accurate Prompt with Strict Aspect Ratio
    prompt_sections = [
        f"Generate a 9:16 vertical aspect ratio (1080x1920), high quality {style_normalized} style scene featuring '{topic}'.",
        "The scene must be warm, vibrant, colorful, cinematic, and tailored for toddlers/children's educational media.",
    ]

    if char_guidelines:
        prompt_sections.append(f"CHARACTER IDENTITY & APPEARANCE RULES ({style_normalized} Style):\n{char_guidelines}")
    else:
        prompt_sections.append("Ensure the main character is consistent, expressive, friendly, and visually engaging.")

    if project_guidelines:
        prompt_sections.append(f"PROJECT CREATIVE GUIDELINES:\n{project_guidelines}")

    if human_feedback and state.get("gate1_decision") == "revise_image":
        prompt_sections.append(f"USER REVISION INSTRUCTIONS (HIGHEST PRIORITY):\n{human_feedback}")

    prompt_sections.append(
        f"Framing & Composition: Vertical 9:16 portrait orientation. "
        f"The main character is joyfully interacting with '{topic}'. "
        f"Clean background, soft studio lighting, ultra-detailed {style_normalized} textures."
    )

    full_prompt = "\n\n".join(prompt_sections)

    tool_args = {
        "prompt": full_prompt,
        "output_path": image_path
    }
    if ref_image_path and os.path.isfile(ref_image_path):
        tool_args["image_path"] = ref_image_path

    res = await generate_image.ainvoke(tool_args)

    file_persisted = bool(image_path and os.path.isfile(image_path) and os.path.getsize(image_path) > 0)

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="🎨 Content Creator",
        event_title="Base Image Generation",
        details={
            "Style": style_normalized,
            "Image Path": image_path,
            "Reference Image Used": ref_image_path or "None",
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
