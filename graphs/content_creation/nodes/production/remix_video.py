import os
import re
import json
from graphs.content_creation.utils.paths import resolve_task_asset
from graphs.content_creation.utils.logging import _append_execution_log
from graphs.content_creation.utils.classifiers import classify_gate2_intent, extract_remix_parameters
from tools.remix_video import remix_video

async def remix_video_task(state: dict) -> dict:
    """Overlays audio track and styled Chinese subtitles onto visual plate using FFmpeg remix_video."""
    if state.get("error_message"):
        return {}

    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_path = state.get("project_path", "")
    output_path = state.get("output_path", "")
    raw_video_path = state.get("raw_video_path") or (os.path.join(output_path, f"{topic}_raw_video.mp4") if output_path else "")

    human_feedback = state.get("latest_human_feedback", "")
    gate2_decision = state.get("gate2_decision") or classify_gate2_intent(human_feedback)
    needs_remix_revision = (
        state.get("gate2_decision") in ["revise_remix", "revise_video", "revise_audio", "revise_subtitles"] or
        bool(human_feedback and gate2_decision in ["revise_remix", "revise_video", "revise_audio", "revise_subtitles"])
    )

    video_path, should_generate = resolve_task_asset(output_path, topic, "video", needs_revision=needs_remix_revision)
    if not should_generate and state.get("video_qc_passed"):
        return {
            "remixed_video_path": video_path,
            "video_persisted": True
        }

    video_plot_path = state.get("video_plot_path") or (os.path.join(output_path, f"{topic}_video_plot.md") if output_path else "")
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_path, "execution_log.md") if output_path else "")

    if not (raw_video_path and os.path.isfile(raw_video_path) and os.path.getsize(raw_video_path) > 0):
        return {
            "remixed_video_path": video_path,
            "video_persisted": False,
            "video_generation_error": f"Raw visual plate not found at '{raw_video_path}'."
        }

    actions = []
    audio_path = state.get("source_audio_path")
    overlay_text = state.get("overlay_text", [])

    plot_data = {}
    try:
        plot_json_path = video_plot_path.replace(".md", ".json")
        with open(plot_json_path, "r") as pf:
            plot_data = json.load(pf)

        src_aud = plot_data.get("source_audio")
        if src_aud:
            aud_cands = [
                src_aud,
                os.path.join(project_path, os.path.basename(src_aud)),
                os.path.join(output_path, os.path.basename(src_aud)),
                os.path.join(project_path, src_aud)
            ]
            for cand in aud_cands:
                if cand and os.path.isfile(cand) and os.path.getsize(cand) > 0:
                    audio_path = cand
                    break
        if plot_data.get("overlay_text"):
            overlay_text = plot_data["overlay_text"]
    except Exception:
        pass

    if not audio_path and output_path:
        import glob
        cands = glob.glob(os.path.join(output_path, f"{topic}*.wav")) + glob.glob(os.path.join(output_path, f"{topic}*.m4a"))
        if cands:
            audio_path = cands[0]

    # Extract dynamic remix parameters from human feedback, state channels, and plot_data
    remix_params = state.get("remix_params") or state.get("remix_parameters") or {}
    feedback_params = extract_remix_parameters(human_feedback) if human_feedback else {}

    # Audio Start Time (audio plays until its natural end)
    audio_start = (
        feedback_params.get("audio_start_time") if feedback_params.get("audio_start_time") is not None
        else remix_params.get("audio_start_time") if remix_params.get("audio_start_time") is not None
        else state.get("audio_start_time") if state.get("audio_start_time") is not None
        else plot_data.get("audio_start_time") if plot_data.get("audio_start_time") is not None
        else 1.5
    )

    # Subtitle / Text Start Time
    text_start = (
        feedback_params.get("text_start_time") if feedback_params.get("text_start_time") is not None
        else remix_params.get("text_start_time") if remix_params.get("text_start_time") is not None
        else state.get("text_start_time") if state.get("text_start_time") is not None
        else plot_data.get("text_start_time") if plot_data.get("text_start_time") is not None
        else audio_start
    )

    # Subtitle End Time (None allows text to display through remainder of video)
    text_end = (
        feedback_params.get("text_end_time") if feedback_params.get("text_end_time") is not None
        else remix_params.get("text_end_time") if remix_params.get("text_end_time") is not None
        else state.get("text_end_time") if state.get("text_end_time") is not None
        else plot_data.get("text_end_time") if plot_data.get("text_end_time") is not None
        else None
    )

    # Subtitle Coordinates & Style (matching tools/remix_video.py schema)
    pos = str(
        feedback_params.get("position") or
        remix_params.get("position") or
        state.get("position") or
        plot_data.get("position") or
        ""
    ).lower().strip()

    x = str(
        feedback_params.get("x") or
        remix_params.get("x") or
        state.get("x") or
        plot_data.get("x") or
        "(w-text_w)/2"
    )

    explicit_y = (
        feedback_params.get("y") or
        remix_params.get("y") or
        state.get("y") or
        plot_data.get("y")
    )
    if explicit_y:
        y = str(explicit_y)
    elif pos == "top":
        y = "h*0.12"
    elif pos in ["center", "middle"]:
        y = "(h-text_h)/2"
    elif pos == "bottom":
        y = "h-text_h-h*0.10"
    else:
        y = "h-text_h-h*0.10"

    font_path = str(
        feedback_params.get("font_path") or
        remix_params.get("font_path") or
        state.get("font_path") or
        plot_data.get("font_path") or
        ""
    )

    font_size = int(
        feedback_params.get("font_size") or
        remix_params.get("font_size") or
        state.get("font_size") or
        plot_data.get("font_size") or
        48
    )

    font_color = str(
        feedback_params.get("font_color") or
        remix_params.get("font_color") or
        state.get("font_color") or
        plot_data.get("font_color") or
        "yellow"
    )

    border_color = str(
        feedback_params.get("border_color") or
        remix_params.get("border_color") or
        state.get("border_color") or
        plot_data.get("border_color") or
        "0x4A3B32"
    )

    border_width = int(
        feedback_params.get("border_width") or
        remix_params.get("border_width") or
        state.get("border_width") or
        plot_data.get("border_width") or
        5
    )

    if audio_path and os.path.isfile(audio_path) and os.path.getsize(audio_path) > 0:
        actions.append({
            "action": "add_audio",
            "audio_path": audio_path,
            "start_time": audio_start,
            "blend_mode": "replace"
        })

    if overlay_text:
        text_str = overlay_text if isinstance(overlay_text, str) else "\n".join(overlay_text)
        text_action = {
            "action": "add_text",
            "text": text_str,
            "start_time": text_start,
            "x": x,
            "y": y,
            "font_size": font_size,
            "font_color": font_color,
            "border_color": border_color,
            "border_width": border_width,
            "box": True,
            "box_color": "black@0.6",
            "box_border_width": 5
        }
        if font_path:
            text_action["font_path"] = font_path
        if text_end is not None:
            text_action["end_time"] = text_end
        actions.append(text_action)

    remix_err = ""
    try:
        res = await remix_video.ainvoke({
            "video_path": raw_video_path,
            "output_path": video_path,
            "actions": actions
        })
        if "<errors>" in str(res) and "</errors>" in str(res):
            tool_err = str(res).split("<errors>")[1].split("</errors>")[0].strip()
            if tool_err and tool_err.lower() != "none":
                remix_err = tool_err
    except Exception as e:
        remix_err = str(e)

    file_persisted = bool(video_path and os.path.isfile(video_path) and os.path.getsize(video_path) > 0)

    _append_execution_log(
        output_path=output_path,
        topic=topic,
        actor="🎬 Content Creator",
        event_title="Video Remix & Audio Muxing",
        details={
            "Target Video Path": video_path,
            "Source Plate": raw_video_path,
            "File Status": f"Verified on disk ({os.path.getsize(video_path)} bytes)" if file_persisted else "FAILED"
        },
        log_path=execution_log_path
    )

    return {
        "remixed_video_path": video_path,
        "remix_actions": actions,
        "video_persisted": file_persisted,
        "video_generation_error": remix_err if not file_persisted else ""
    }
