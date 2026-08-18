import os
import json
from graphs.content_creation.utils.paths import normalize_project_path, _resolve_asset_path
from graphs.content_creation.utils.logging import _append_execution_log
from graphs.content_creation.utils.classifiers import classify_gate1_intent
from graphs.content_creation.schemas import VideoPlot

async def draft_plot_task(state: dict) -> dict:
    """Drafts Video Plot by delegating to LLM with instructions dynamically loaded from project_dir."""
    if state.get("error_message"):
        return {}

    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir") or (os.path.join(project_dir, topic) if project_dir else ""))
    creator_instructions_path = state.get("creator_instructions_path", "")
    manifest_path = state.get("manifest_path", "")
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")
    feedback = state.get("video_plot_feedback")
    human_feedback = state.get("latest_human_feedback")
    gate1_decision = state.get("gate1_decision")
    if human_feedback and (not gate1_decision or gate1_decision == "approved"):
        gate1_decision = classify_gate1_intent(human_feedback)

    existing_plot = _resolve_asset_path(output_dir, topic, "video_plot", next_version=False)
    needs_plot_revision = (
        gate1_decision in ["revise_plot", "revise_image"] or
        state.get("qc_rejection_target") == "plot" or
        bool(feedback and not state.get("video_plot_qc_passed")) or
        "TARGET: PLOT" in str(feedback or "").upper() or
        "VIDEO PLOT" in str(feedback or "").upper() or
        bool(human_feedback and gate1_decision != "approved")
    )

    if os.path.exists(existing_plot) and not needs_plot_revision and state.get("video_plot_qc_passed"):
        return {
            "project_dir": project_dir,
            "output_dir": output_dir,
            "video_plot_path": existing_plot
        }

    if os.path.exists(existing_plot) and needs_plot_revision:
        video_plot_path = _resolve_asset_path(output_dir, topic, "video_plot", next_version=True)
    else:
        video_plot_path = existing_plot
    video_plot_json_path = video_plot_path.replace(".md", ".json")

    image_path = state.get("image_path", "")
    audio_path = state.get("source_audio_path") or (os.path.join(output_dir, f"{topic}_wav.wav") if output_dir else f"{topic}_wav.wav")

    # 1. Load Creator & Project Instructions dynamically from project_dir
    instructions_text = ""
    for path in [manifest_path, creator_instructions_path]:
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    instructions_text += f"\n--- {os.path.basename(path)} ---\n" + f.read()
            except Exception:
                pass

    # 2. Read Style-Specific Character Sheet from project_dir
    style = str(state.get("style") or "3D").strip()
    style_str = "3D Animation" if style.lower() == "3d" else style.capitalize()
    char_guidelines = ""
    char_dir = os.path.join(project_dir, "character") if project_dir else ""
    if char_dir and os.path.isdir(char_dir):
        for fname in sorted(os.listdir(char_dir)):
            if fname.lower().endswith(".md") and style.lower() in fname.lower():
                target_sheet_path = os.path.join(char_dir, fname)
                try:
                    with open(target_sheet_path, "r", encoding="utf-8") as f:
                        char_guidelines = f"\n--- {os.path.basename(target_sheet_path)} ---\n" + f.read()
                    break
                except Exception:
                    pass

    # 3. Assemble LLM Prompt strictly from loaded markdown instructions
    prompt_sections = [
        "You are the Content Creator. Draft the structured Video Plot strictly following the creator instructions and character guidelines below:\n"
    ]
    if instructions_text:
        prompt_sections.append(f"--- PROJECT CREATOR INSTRUCTIONS ---\n{instructions_text}\n")
    if char_guidelines:
        prompt_sections.append(f"--- CHARACTER IDENTITY GUIDELINES ({style_str}) ---\n{char_guidelines}\n")

    prompt_sections.append(
        f"TASK & DATA BINDING:\n"
        f"- Topic / Word: `{topic}`\n"
        f"- Episode Style: `{style_str}`\n"
        f"- Source Image: `{image_path}`\n"
        f"- Source Audio: `{audio_path}`\n"
        f"Strictly adhere to the motion prompt rules, phonetic mouth articulation, and standardized template in the instructions."
    )
    if feedback:
        prompt_sections.append(f"--- BRAND QC FEEDBACK TO FIX ---\n{feedback}\n")
    if human_feedback:
        prompt_sections.append(f"--- HUMAN REVISION FEEDBACK (HIGHEST PRIORITY) ---\n{human_feedback}\n")

    prompt = "\n".join(prompt_sections)

    overlay_text = ""
    video_plot_content = ""
    try:
        from tools.agent_call import agent_call
        import re

        channel = state.get("channel") or "content-creation"
        tool_res = await agent_call.ainvoke({
            "agent_id": "content-creator",
            "prompt": prompt,
            "channel": channel
        })

        payload = ""
        m = re.search(r"<payload>(.*?)</payload>", str(tool_res), re.DOTALL)
        if m:
            payload = m.group(1).strip()
        else:
            payload = str(tool_res).strip()

        video_plot_content = payload
        plot_dict = {
            "title": f"{topic.capitalize()} Video Plot",
            "source_image": image_path,
            "source_audio": audio_path,
            "motion_prompt": "",
            "overlay_text": "",
            "markdown_content": payload
        }

        try:
            data = json.loads(payload)
            if isinstance(data, dict):
                plot_dict.update(data)
                video_plot_content = data.get("markdown_content") or payload
                overlay_text = data.get("overlay_text", "")
        except Exception:
            pass

        if not overlay_text:
            for line in video_plot_content.splitlines():
                if "overlay text:" in line.lower() or "text overlay:" in line.lower():
                    overlay_text = line.split(":", 1)[-1].strip(" `\"'")
                    break

        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            with open(video_plot_path, "w", encoding="utf-8") as f:
                f.write(video_plot_content)

            with open(video_plot_json_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(plot_dict, indent=2))

    except Exception as e:
        print(f"draft_plot_task: Error executing agent_call for content-creator: {e}")
        video_plot_content = ""

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="📝 Content Creator",
        event_title="Video Plot Drafting",
        details={
            "Video Plot MD Path": video_plot_path,
            "Video Plot JSON Path": video_plot_json_path,
            "Video Plot Preview": video_plot_content[:300] + ("..." if len(video_plot_content) > 300 else "")
        },
        log_path=execution_log_path
    )

    return {
        "video_plot_path": video_plot_path,
        "overlay_text": overlay_text
    }
