import os
import json
from graphs.content_creation.utils.paths import normalize_project_path, _resolve_asset_path
from graphs.content_creation.utils.logging import _append_execution_log
from tools.remix_video import remix_video

async def remix_video_task(state: dict) -> dict:
    """Overlays audio track and styled Chinese subtitles onto visual plate using FFmpeg remix_video."""
    if state.get("error_message"):
        return {}

    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir") or (os.path.join(project_dir, topic) if project_dir else ""))
    raw_video_path = state.get("raw_video_path") or _resolve_asset_path(output_dir, topic, "raw_video", next_version=False)
    video_path = _resolve_asset_path(output_dir, topic, "video", next_version=True)
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
        await remix_video.ainvoke({
            "input_video_path": raw_video_path,
            "output_video_path": video_path,
            "actions": actions
        })
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
