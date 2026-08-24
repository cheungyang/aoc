import os
import re
import json
from graphs.content_creation.utils.paths import resolve_task_asset, load_project_context
from graphs.content_creation.utils.logging import _append_execution_log
from graphs.content_creation.utils.classifiers import classify_gate1_intent
from graphs.content_creation.prompts import build_draft_plot_prompt

async def draft_plot_task(state: dict) -> dict:
    """Drafts Video Plot by delegating to LLM with instructions dynamically loaded from project_path."""
    if state.get("error_message"):
        return {}

    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    style = str(state.get("style") or "3D").strip()
    project_path = state.get("project_path", "")
    output_path = state.get("output_path", "")
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_path, "execution_log.md") if output_path else "")
    feedback = state.get("video_plot_feedback")
    human_feedback = state.get("latest_human_feedback")
    gate1_decision = state.get("gate1_decision")
    if human_feedback and (not gate1_decision or gate1_decision == "approved"):
        gate1_decision = classify_gate1_intent(human_feedback)

    needs_plot_revision = (
        gate1_decision == "revise_plot" or
        state.get("qc_rejection_target") == "plot" or
        bool(feedback and not state.get("video_plot_qc_passed")) or
        "TARGET: PLOT" in str(feedback or "").upper() or
        "VIDEO PLOT" in str(feedback or "").upper() or
        bool(human_feedback and gate1_decision == "revise_plot")
    )

    video_plot_path, should_generate = resolve_task_asset(output_path, topic, "video_plot", needs_revision=needs_plot_revision)
    if not should_generate and state.get("video_plot_qc_passed"):
        return {
            "project_path": project_path,
            "output_path": output_path,
            "video_plot_path": video_plot_path
        }

    video_plot_json_path = video_plot_path.replace(".md", ".json")
    image_path = state.get("image_path", "")
    audio_path = state.get("source_audio_path") or state.get("audio_path") or (os.path.join(output_path, f"{topic}_wav.wav") if output_path else f"{topic}_wav.wav")

    ctx = load_project_context(
        project_path=project_path,
        style=style,
        manifest_path=state.get("manifest_path", ""),
        creator_instructions_path=state.get("creator_instructions_path", "")
    )
    style_str = "3D Animation" if style.lower() == "3d" else ctx["style_normalized"]
    prompt = build_draft_plot_prompt(
        topic=topic,
        style_str=style_str,
        image_path=image_path,
        audio_path=audio_path,
        project_path=project_path,
        output_path=output_path,
        video_plot_path=video_plot_path,
        video_plot_json_path=video_plot_json_path,
        project_guidelines=ctx.get("project_guidelines", ""),
        char_guidelines=ctx.get("char_guidelines", ""),
        feedback=feedback or "",
        human_feedback=human_feedback or ""
    )
    overlay_text = ""
    video_plot_content = ""

    try:
        from tools.agent_call import agent_call
        channel = state.get("channel") or "content-creation"
        tool_res = await agent_call.ainvoke({
            "agent_id": "graph-worker",
            "prompt": prompt,
            "channel": channel
        })

        m = re.search(r"<payload>(.*?)</payload>", str(tool_res), re.DOTALL)
        payload = m.group(1).strip() if m else str(tool_res).strip()

        # 1. Direct XML tag extraction for zero-overhead, certain parsing
        status_m = re.search(r"<status>(.*?)</status>", payload, re.DOTALL)
        status = status_m.group(1).strip() if status_m else "success"
        error_m = re.search(r"<error>(.*?)</error>", payload, re.DOTALL)
        error = error_m.group(1).strip() if error_m else ""

        motion_m = re.search(r"<motion_prompt>(.*?)</motion_prompt>", payload, re.DOTALL)
        overlay_m = re.search(r"<overlay_text>(.*?)</overlay_text>", payload, re.DOTALL)
        md_m = re.search(r"<markdown_content>(.*?)</markdown_content>", payload, re.DOTALL)
        title_m = re.search(r"<title>(.*?)</title>", payload, re.DOTALL)

        title_val = title_m.group(1).strip() if title_m else f"{topic.capitalize()} Video Plot"
        if overlay_m:
            overlay_text = overlay_m.group(1).strip(" `\"'")

        motion_prompt = motion_m.group(1).strip() if motion_m else ""

        if md_m:
            video_plot_content = md_m.group(1).strip()
        elif os.path.isfile(video_plot_path):
            try:
                with open(video_plot_path, "r", encoding="utf-8") as f:
                    video_plot_content = f.read()
            except Exception:
                video_plot_content = ""

        if not video_plot_content:
            video_plot_content = f"# {title_val}\n\n- **Motion Prompt**: {motion_prompt}\n- **Overlay Text**: {overlay_text}\n"

        plot_dict = {
            "title": title_val,
            "source_image": image_path,
            "source_audio": audio_path,
            "motion_prompt": motion_prompt,
            "overlay_text": overlay_text,
            "markdown_content": video_plot_content
        }

        # 2. Fallback: JSON parsing
        if not md_m and not motion_m:
            try:
                data = json.loads(payload)
                if isinstance(data, dict):
                    plot_dict.update(data)
                    video_plot_content = data.get("markdown_content") or video_plot_content
                    overlay_text = data.get("overlay_text", "") or overlay_text
            except Exception:
                pass

        # 3. Fallback: Line-by-line search for overlay text
        if not overlay_text:
            for line in video_plot_content.splitlines():
                if "overlay text:" in line.lower() or "text overlay:" in line.lower():
                    overlay_text = line.split(":", 1)[-1].strip(" `\"'")
                    plot_dict["overlay_text"] = overlay_text
                    break

        if output_path:
            os.makedirs(output_path, exist_ok=True)
            with open(video_plot_path, "w", encoding="utf-8") as f:
                f.write(video_plot_content)
            with open(video_plot_json_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(plot_dict, indent=2))

    except Exception as e:
        print(f"draft_plot_task: Error executing agent_call for graph-worker: {e}")
        video_plot_content = ""

    _append_execution_log(
        output_path=output_path,
        topic=topic,
        actor="⚙️ Graph Worker (Video Plot Drafting)",
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
