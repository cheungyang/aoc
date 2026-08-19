import os
import re
import json
from graphs.content_creation.utils.paths import normalize_project_path, _resolve_asset_path, canonicalize_output_dir, resolve_task_asset
from graphs.content_creation.utils.logging import _append_execution_log
from graphs.content_creation.utils.classifiers import classify_gate2_intent
from tools.remix_video import remix_video

async def remix_video_task(state: dict) -> dict:
    """Overlays audio track and styled Chinese subtitles onto visual plate using FFmpeg remix_video."""
    if state.get("error_message"):
        return {}

    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = canonicalize_output_dir(project_dir, state.get("output_dir"), topic)
    raw_video_path = normalize_project_path(state.get("raw_video_path"))

    # Resilient resolution of raw_video_path under canonical output_dir
    if not raw_video_path or not (os.path.isfile(raw_video_path) and os.path.getsize(raw_video_path) > 0):
        cand = _resolve_asset_path(output_dir, topic, "raw_video", next_version=False)
        if cand and os.path.isfile(cand) and os.path.getsize(cand) > 0:
            raw_video_path = cand
        elif raw_video_path and output_dir:
            cand2 = os.path.join(output_dir, os.path.basename(raw_video_path))
            if os.path.isfile(cand2) and os.path.getsize(cand2) > 0:
                raw_video_path = cand2
        else:
            raw_video_path = _resolve_asset_path(output_dir, topic, "raw_video", next_version=False)

    human_feedback = state.get("latest_human_feedback", "")
    gate2_decision = state.get("gate2_decision") or classify_gate2_intent(human_feedback)
    needs_remix_revision = (
        state.get("gate2_decision") in ["revise_remix", "revise_video", "revise_audio", "revise_subtitles"] or
        bool(human_feedback and gate2_decision in ["revise_remix", "revise_video", "revise_audio", "revise_subtitles"]) or
        bool(raw_video_path and re.search(r'_v\d+', os.path.basename(raw_video_path)))
    )

    video_path, should_generate = resolve_task_asset(output_dir, topic, "video", needs_revision=needs_remix_revision)
    if not should_generate and state.get("video_qc_passed"):
        return {
            "remixed_video_path": video_path,
            "video_persisted": True
        }

    video_plot_path = state.get("video_plot_path") or _resolve_asset_path(output_dir, topic, "video_plot", next_version=False)
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")

    if not (raw_video_path and os.path.isfile(raw_video_path) and os.path.getsize(raw_video_path) > 0):
        return {
            "remixed_video_path": video_path,
            "video_persisted": False,
            "video_generation_error": f"Raw visual plate not found at '{raw_video_path}'."
        }

    actions = []
    audio_path = state.get("source_audio_path")
    overlay_text = state.get("overlay_text", [])

    try:
        plot_json_path = video_plot_path.replace(".md", ".json")
        with open(plot_json_path, "r") as pf:
            plot_data = json.load(pf)

        src_aud = plot_data.get("source_audio")
        if src_aud:
            aud_cands = [
                src_aud,
                os.path.join(project_dir, os.path.basename(src_aud)),
                os.path.join(output_dir, os.path.basename(src_aud)),
                os.path.join(project_dir, src_aud)
            ]
            for cand in aud_cands:
                if cand and os.path.isfile(cand) and os.path.getsize(cand) > 0:
                    audio_path = cand
                    break
        if plot_data.get("overlay_text"):
            overlay_text = plot_data["overlay_text"]
    except Exception:
        pass

    if not audio_path and output_dir:
        import glob
        cands = glob.glob(os.path.join(output_dir, f"{topic}*.wav")) + glob.glob(os.path.join(output_dir, f"{topic}*.m4a"))
        if cands:
            audio_path = cands[0]

    if audio_path and os.path.isfile(audio_path) and os.path.getsize(audio_path) > 0:
        actions.append({
            "action": "add_audio",
            "audio_path": audio_path,
            "start_time": 1.5,
            "mix_mode": "replace"
        })

    if overlay_text:
        text_str = overlay_text if isinstance(overlay_text, str) else "\n".join(overlay_text)
        actions.append({
            "action": "add_text",
            "text": text_str,
            "start_time": 1.5,
            "end_time": 4.0,
            "position": "bottom",
            "font_size": 48,
            "font_color": "yellow",
            "box": True,
            "box_color": "black@0.6",
            "box_border_width": 5
        })

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
        output_dir=output_dir,
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
