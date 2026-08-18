import os
import json
from graphs.content_creation.utils.paths import normalize_project_path, _resolve_asset_path
from graphs.content_creation.utils.logging import _append_execution_log
from graphs.content_creation.schemas import VideoPlot

async def draft_plot_task(state: dict) -> dict:
    """Drafts the Video Plot for the topic using Gemini structured output and dual-publishes .md and .json."""
    if state.get("error_message"):
        return {}

    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir") or (os.path.join(project_dir, topic) if project_dir else ""))
    creator_instructions_path = state.get("creator_instructions_path", "")
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")

    feedback = state.get("video_plot_feedback")
    human_feedback = state.get("latest_human_feedback")

    existing_plot = _resolve_asset_path(output_dir, topic, "video_plot", next_version=False)
    needs_plot_revision = (
        state.get("gate1_decision") == "revise_plot" or
        state.get("qc_rejection_target") == "plot" or
        bool(feedback)
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

    instructions_text = ""
    try:
        with open(creator_instructions_path, "r", encoding="utf-8") as f:
            instructions_text = f.read()
    except Exception:
        pass

    # Read Style-Specific Character Sheet
    style = str(state.get("style") or "3D").strip()
    style_normalized = style.upper() if style.lower() == "3d" else style.capitalize()
    char_guidelines = ""
    char_dir = os.path.join(project_dir, "character") if project_dir else ""
    if char_dir and os.path.isdir(char_dir):
        target_sheet_name = f"01_Character_Sheet_{style_normalized}.md"
        target_sheet_path = os.path.join(char_dir, target_sheet_name)
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

    prompt = (
        f"You are the Content Creator.\n"
        f"--- CREATOR INSTRUCTIONS ---\n{instructions_text}\n----------------------------\n"
    )
    if char_guidelines:
        prompt += f"--- CHARACTER IDENTITY GUIDELINES ---\n{char_guidelines}\n-------------------------------------\n"
    prompt += (
        f"Draft the Video Plot for the topic '{topic}' strictly following the template and constraints defined in the instructions.\n\n"
        f"IMPORTANT DATA BINDING:\n"
        f"- Use this exact path for the Source Image field: `{image_path}`\n"
        f"- Use this exact path for the Source Audio field: `{audio_path}`\n"
    )
    if feedback:
        prompt += f"\nPrevious Brand Editor Feedback to fix:\n{feedback}\n"
    if human_feedback and state.get("gate1_decision") == "revise_plot":
        prompt += f"\nHuman HITL Revision Feedback for Video Plot:\n{human_feedback}\n"

    try:
        from core.loaders.agents_loader import AgentsLoader
        from langchain_google_genai import ChatGoogleGenerativeAI

        config = AgentsLoader()._agent_configs.get("content-creator", {})
        model_name = config.get("model", "gemini-3.7-flash")

        llm = ChatGoogleGenerativeAI(model=model_name).with_structured_output(VideoPlot)
        plot_data: VideoPlot = await llm.ainvoke(prompt)

        video_plot_content = plot_data.markdown_content

        if video_plot_path:
            os.makedirs(os.path.dirname(video_plot_path), exist_ok=True)
            with open(video_plot_path, "w", encoding="utf-8") as f:
                f.write(video_plot_content)

            with open(video_plot_json_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(plot_data.model_dump(), indent=2))

    except Exception as e:
        print(f"draft_plot_task: Error saving video plot file: {e}")
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
        "overlay_text": (plot_data.overlay_text if isinstance(plot_data.overlay_text, str) else "\n".join(plot_data.overlay_text)) if 'plot_data' in locals() and plot_data.overlay_text else ""
    }
