import os
import re
import json
from graphs.content_creation.utils.paths import normalize_path, canonicalize_output_dir, resolve_task_asset, load_project_context
from graphs.content_creation.utils.logging import _append_execution_log
from graphs.content_creation.utils.classifiers import classify_gate1_intent
from graphs.content_creation.prompts import build_draft_plot_prompt

async def draft_plot_task(state: dict) -> dict:
    """Drafts Video Plot by delegating to LLM with instructions dynamically loaded from project_dir."""
    if state.get("error_message"):
        return {}

    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    style = str(state.get("style") or "3D").strip()
    project_dir = normalize_path(state.get("project_dir", ""))
    output_dir = canonicalize_output_dir(project_dir, state.get("output_dir"), topic)
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")
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

    video_plot_path, should_generate = resolve_task_asset(output_dir, topic, "video_plot", needs_revision=needs_plot_revision)
    if not should_generate and state.get("video_plot_qc_passed"):
        return {
            "project_dir": project_dir,
            "output_dir": output_dir,
            "video_plot_path": video_plot_path
        }

    video_plot_json_path = video_plot_path.replace(".md", ".json")
    image_path = state.get("image_path", "")
    audio_path = state.get("source_audio_path") or (os.path.join(output_dir, f"{topic}_wav.wav") if output_dir else f"{topic}_wav.wav")

    ctx = load_project_context(
        project_dir=project_dir,
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
        project_dir=project_dir,
        output_dir=output_dir,
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
        print(f"draft_plot_task: Error executing agent_call for graph-worker: {e}")
        video_plot_content = ""

    _append_execution_log(
        output_dir=output_dir,
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
