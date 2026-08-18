import os
import re
from graphs.content_creation.utils.paths import normalize_project_path, _resolve_asset_path
from graphs.content_creation.utils.logging import _append_execution_log
from graphs.content_creation.utils.classifiers import classify_gate1_intent
from tools.generate_image import generate_image

async def generate_image_task(state: dict) -> dict:
    """Generates 1-shot base image using instructions and character sheets loaded dynamically from project_dir."""
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
    gate1_decision = state.get("gate1_decision")
    if human_feedback and (not gate1_decision or gate1_decision == "approved"):
        gate1_decision = classify_gate1_intent(human_feedback)

    existing_image = _resolve_asset_path(output_dir, topic, "image", next_version=False)
    needs_image_revision = (
        gate1_decision == "revise_image" or
        state.get("qc_rejection_target") == "image" or
        "TARGET: IMAGE" in str(state.get("video_plot_feedback") or "").upper() or
        "BASE IMAGE" in str(state.get("video_plot_feedback") or "").upper() or
        bool(human_feedback and gate1_decision not in ["approved", "revise_plot"])
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

    # 1. Load Style-Specific Character Sheet & Frontmatter Reference Image from project_dir
    char_dir = os.path.join(project_dir, "character") if project_dir else ""
    ref_image_path = state.get("reference_image_path")
    char_guidelines = ""

    if char_dir and os.path.isdir(char_dir):
        for fname in sorted(os.listdir(char_dir)):
            if fname.lower().endswith(".md") and style.lower() in fname.lower():
                sheet_path = os.path.join(char_dir, fname)
                try:
                    with open(sheet_path, "r", encoding="utf-8") as f:
                        raw_text = f.read()

                    # Extract YAML frontmatter
                    m_fm = re.match(r'^---\s*\n(.*?)\n---\s*\n', raw_text, re.DOTALL)
                    if m_fm:
                        fm_text = m_fm.group(1)
                        m_ref = re.search(r'reference_image:\s*["\']?([^"\'\r\n]+)["\']?', fm_text, re.IGNORECASE)
                        if m_ref and not ref_image_path:
                            cand_ref = os.path.join(char_dir, os.path.basename(m_ref.group(1).strip()))
                            if os.path.isfile(cand_ref):
                                ref_image_path = cand_ref
                        char_guidelines = raw_text[m_fm.end():].strip()
                    else:
                        char_guidelines = raw_text.strip()
                    break
                except Exception:
                    pass

    # Check if human feedback explicitly references a specific image file in char_dir or project_dir
    if human_feedback:
        m_custom_ref = re.search(r'(?:reference|ref|character)[^\w\n]*([\w\d_./-]+\.(?:jpg|jpeg|png|webp))', human_feedback, re.IGNORECASE)
        if m_custom_ref:
            custom_ref_file = m_custom_ref.group(1).strip()
            cands = [
                os.path.join(char_dir, os.path.basename(custom_ref_file)) if char_dir else "",
                os.path.join(project_dir, custom_ref_file) if project_dir else "",
                custom_ref_file
            ]
            for cand in cands:
                if cand and os.path.isfile(cand):
                    ref_image_path = cand
                    break

    # 2. Load Project & Creator Instructions from project_dir
    project_guidelines = ""
    for path in [manifest_path, creator_instructions_path]:
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    project_guidelines += f"\n--- {os.path.basename(path)} ---\n" + f.read()
            except Exception:
                pass

    # 3. Assemble Dynamic Prompt Strictly from Loaded Project Instructions (No hardcoded prompts)
    prompt_sections = []
    if char_guidelines:
        prompt_sections.append(f"--- CHARACTER IDENTITY & APPEARANCE RULES ({style_normalized} Style) ---\n{char_guidelines}")
    if project_guidelines:
        prompt_sections.append(f"--- PROJECT CREATIVE & CREATOR INSTRUCTIONS ---\n{project_guidelines}")

    prompt_sections.append(
        f"TASK: Generate the 1-shot base image for topic: '{topic}', style: '{style_normalized}'.\n"
        f"Strictly adhere to the character appearance rules, costume requirements, and scene composition instructions defined above."
    )

    if human_feedback:
        prompt_sections.append(f"--- HUMAN REVISION INSTRUCTIONS (HIGHEST PRIORITY) ---\n{human_feedback}")

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
