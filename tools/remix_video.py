import os
import json
import shutil
import subprocess
from typing import Union, List, Dict, Any, Optional
from langchain_core.tools import tool
from core.util import format_tool_response


def _get_ffmpeg_executable() -> Optional[str]:
    """Finds ffmpeg executable from imageio_ffmpeg or system PATH."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    return shutil.which("ffmpeg")


def _has_audio_stream(ffmpeg_exe: str, video_path: str) -> bool:
    """Checks whether the input video file contains an audio stream."""
    try:
        cmd = [ffmpeg_exe, "-i", video_path]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        for line in res.stderr.splitlines():
            if "Stream #" in line and "Audio:" in line:
                return True
    except Exception:
        pass
    return False


def _parse_timestamp_sec(ts: Union[float, int, str, None]) -> Optional[float]:
    """Parses a timestamp input (seconds as float/int or time strings) into seconds float."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        sec = float(ts)
        if sec < 0:
            raise ValueError(f"Timestamp cannot be negative: {ts}")
        return sec
    if isinstance(ts, str):
        s = ts.strip().lower()
        if not s:
            return None

        # Handle millisecond suffix e.g. "2500ms"
        if s.endswith("ms"):
            ms_val = float(s[:-2].strip())
            if ms_val < 0:
                raise ValueError(f"Timestamp cannot be negative: {ts}")
            return ms_val / 1000.0

        # Strip descriptive unit suffixes
        if s.endswith("seconds"):
            s = s[:-7].strip()
        elif s.endswith("second"):
            s = s[:-6].strip()
        elif s.endswith("secs"):
            s = s[:-4].strip()
        elif s.endswith("sec"):
            s = s[:-3].strip()
        elif s.endswith("s") and len(s) > 1 and (s[-2].isdigit() or s[-2] == '.'):
            s = s[:-1].strip()

        # Handle colon-separated time: HH:MM:SS or MM:SS
        if ":" in s:
            parts = s.split(":")
            if len(parts) == 3:
                h, m, sec = float(parts[0]), float(parts[1]), float(parts[2])
                total = h * 3600.0 + m * 60.0 + sec
            elif len(parts) == 2:
                m, sec = float(parts[0]), float(parts[1])
                total = m * 60.0 + sec
            else:
                raise ValueError(f"Invalid timestamp format: '{ts}'")
            if total < 0:
                raise ValueError(f"Timestamp cannot be negative: {ts}")
            return total
        else:
            total = float(s)
            if total < 0:
                raise ValueError(f"Timestamp cannot be negative: {ts}")
            return total

    raise ValueError(f"Unsupported timestamp type: {type(ts)}")


def _resolve_font_file(user_font_path: Optional[str] = None) -> Optional[str]:
    """Resolves an existing font file path with fallback to macOS / Linux Traditional Chinese fonts."""
    if user_font_path and os.path.exists(user_font_path):
        return os.path.abspath(user_font_path)

    # Standard candidate fonts for Traditional Chinese / CJK typography
    candidate_fonts = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansTC-Regular.otf",
        "/Library/Fonts/Arial.ttf",
    ]

    for candidate in candidate_fonts:
        if os.path.exists(candidate):
            return candidate

    return None


def _escape_drawtext_str(s: str) -> str:
    """Escapes special characters in text for ffmpeg drawtext filter."""
    s = s.replace("\\", "\\\\")
    s = s.replace(":", "\\:")
    s = s.replace("'", "'\\''")
    s = s.replace("%", "\\%")
    return s


@tool
def remix_video(
    video_path: str,
    actions: Union[str, List[Dict[str, Any]]],
    output_path: str,
    agent_id: Optional[str] = None,
) -> str:
    """Remix a video file by overlaying audio clips and text overlays using ffmpeg.

    Applies a sequence of editing actions (such as adding audio tracks with volume control
    and adding styled text overlays supporting Traditional Chinese) to a source video in a
    single-pass ffmpeg execution.

    Args:
        video_path: Local file path to the source video (e.g. .mp4, .mov).
        actions: List of action dictionaries (or a JSON string array) specifying edits:
            - {"action": "add_audio", "audio_path": str, "start_time": float|str, "volume": float, "blend_mode": "blend"|"replace", "original_volume": float}
            - {"action": "add_text", "text": str, "start_time": float|str, "end_time": float|str, "font_path": str, "font_size": int, "font_color": str, "border_color": str, "border_width": int, "x": str, "y": str}
        output_path: Destination file path for the remixed video output (e.g. "output.mp4").
        agent_id: Optional ID of the calling agent.

    Returns:
        The formatted tool response containing the absolute path to the remixed video file.
    """
    if not video_path or not str(video_path).strip():
        return format_tool_response(
            "remix_video",
            payload="",
            errors="Error: video_path cannot be empty."
        )

    if not os.path.exists(video_path):
        return format_tool_response(
            "remix_video",
            payload="",
            errors=f"Error: Video file not found at '{video_path}'."
        )

    if not os.path.isfile(video_path):
        return format_tool_response(
            "remix_video",
            payload="",
            errors=f"Error: Path '{video_path}' is not a valid file."
        )

    if not output_path or not str(output_path).strip():
        return format_tool_response(
            "remix_video",
            payload="",
            errors="Error: output_path cannot be empty."
        )

    # Parse actions if passed as JSON string
    parsed_actions: List[Dict[str, Any]] = []
    if isinstance(actions, str):
        s_act = actions.strip()
        if not s_act:
            parsed_actions = []
        else:
            try:
                parsed = json.loads(s_act)
                if isinstance(parsed, list):
                    parsed_actions = parsed
                elif isinstance(parsed, dict):
                    parsed_actions = [parsed]
                else:
                    return format_tool_response(
                        "remix_video",
                        payload="",
                        errors="Error: 'actions' must be a list of action objects or valid JSON list string."
                    )
            except Exception as e:
                return format_tool_response(
                    "remix_video",
                    payload="",
                    errors=f"Error parsing actions JSON string: {e}"
                )
    elif isinstance(actions, list):
        parsed_actions = actions
    else:
        return format_tool_response(
            "remix_video",
            payload="",
            errors=f"Error: Unsupported actions type '{type(actions)}'. Must be list or JSON string."
        )

    ffmpeg_exe = _get_ffmpeg_executable()
    if not ffmpeg_exe:
        return format_tool_response(
            "remix_video",
            payload="",
            errors="Error: ffmpeg executable could not be found. Please ensure ffmpeg or imageio_ffmpeg is installed."
        )

    has_orig_audio = _has_audio_stream(ffmpeg_exe, video_path)

    # Validate action inputs
    audio_actions: List[Dict[str, Any]] = []
    text_actions: List[Dict[str, Any]] = []

    for i, act in enumerate(parsed_actions):
        if not isinstance(act, dict):
            return format_tool_response(
                "remix_video",
                payload="",
                errors=f"Error: Action at index {i} must be a dictionary."
            )
        action_name = act.get("action", "").lower().strip()
        if action_name == "add_audio":
            audio_path = act.get("audio_path")
            if not audio_path or not os.path.exists(audio_path):
                return format_tool_response(
                    "remix_video",
                    payload="",
                    errors=f"Error in action {i} (add_audio): Audio file not found at '{audio_path}'."
                )
            audio_actions.append(act)
        elif action_name == "add_text":
            text = act.get("text")
            if text is None or not str(text):
                return format_tool_response(
                    "remix_video",
                    payload="",
                    errors=f"Error in action {i} (add_text): 'text' parameter is required and cannot be empty."
                )
            text_actions.append(act)
        else:
            return format_tool_response(
                "remix_video",
                payload="",
                errors=f"Error: Unsupported action type '{action_name}' at index {i}."
            )

    # Build FFmpeg command inputs and filtergraph
    input_args = ["-i", video_path]
    filter_complex_parts: List[str] = []

    # 1. Process Audio Actions
    audio_inputs_count = len(audio_actions)
    for idx, a_act in enumerate(audio_actions, start=1):
        input_args.extend(["-i", a_act["audio_path"]])
        try:
            start_sec = _parse_timestamp_sec(a_act.get("start_time", 0.0)) or 0.0
        except Exception as e:
            return format_tool_response(
                "remix_video",
                payload="",
                errors=f"Error parsing start_time in add_audio action: {e}"
            )
        delay_ms = int(round(start_sec * 1000))
        vol = float(a_act.get("volume", 1.8))
        filter_complex_parts.append(f"[{idx}:a]adelay={delay_ms}|{delay_ms},volume={vol}[a_{idx}]")

    # Determine audio output stream
    mapped_audio_label: Optional[str] = None
    if audio_inputs_count > 0:
        audio_mix_streams: List[str] = []
        # Check if we should blend with original video audio
        blend_mode = audio_actions[0].get("blend_mode", "blend").lower()
        if has_orig_audio and blend_mode != "replace":
            orig_vol = float(audio_actions[0].get("original_volume", 0.6))
            filter_complex_parts.append(f"[0:a]volume={orig_vol}[a_orig]")
            audio_mix_streams.append("[a_orig]")

        for idx in range(1, audio_inputs_count + 1):
            audio_mix_streams.append(f"[a_{idx}]")

        if len(audio_mix_streams) > 1:
            inputs_str = "".join(audio_mix_streams)
            filter_complex_parts.append(
                f"{inputs_str}amix=inputs={len(audio_mix_streams)}:duration=first:dropout_transition=0[aout]"
            )
            mapped_audio_label = "[aout]"
        elif len(audio_mix_streams) == 1:
            mapped_audio_label = audio_mix_streams[0]
    elif has_orig_audio:
        # No audio actions added, keep original audio
        mapped_audio_label = "0:a"

    # 2. Process Video / Text Actions
    mapped_video_label: Optional[str] = None
    if text_actions:
        current_v_label = "[0:v]"
        for idx, t_act in enumerate(text_actions, start=1):
            text_str = _escape_drawtext_str(str(t_act.get("text", "")))
            try:
                start_sec = _parse_timestamp_sec(t_act.get("start_time", 0.0)) or 0.0
                end_sec = _parse_timestamp_sec(t_act.get("end_time"))
            except Exception as e:
                return format_tool_response(
                    "remix_video",
                    payload="",
                    errors=f"Error parsing start_time/end_time in add_text action: {e}"
                )

            font_path = _resolve_font_file(t_act.get("font_path") or t_act.get("fontfile") or t_act.get("font"))
            font_size = int(t_act.get("font_size") or t_act.get("fontsize", 110))
            font_color = str(t_act.get("font_color") or t_act.get("fontcolor", "white"))
            border_color = str(t_act.get("border_color") or t_act.get("bordercolor", "0x4A3B32"))
            border_w = int(t_act.get("border_width") or t_act.get("borderwidth") or t_act.get("borderw", 8))
            x_pos = str(t_act.get("x", "(w-text_w)/2"))
            y_pos = str(t_act.get("y", "h*0.22"))

            drawtext_opts = []
            if font_path:
                drawtext_opts.append(f"fontfile='{font_path}'")
            drawtext_opts.append(f"text='{text_str}'")
            drawtext_opts.append(f"fontsize={font_size}")
            drawtext_opts.append(f"fontcolor={font_color}")
            if border_w > 0:
                drawtext_opts.append(f"bordercolor={border_color}")
                drawtext_opts.append(f"borderw={border_w}")
            drawtext_opts.append(f"x={x_pos}")
            drawtext_opts.append(f"y={y_pos}")

            if end_sec is not None and end_sec > start_sec:
                drawtext_opts.append(f"enable='between(t,{start_sec:.3f},{end_sec:.3f})'")
            elif start_sec > 0.0:
                drawtext_opts.append(f"enable='gte(t,{start_sec:.3f})'")

            if t_act.get("box"):
                drawtext_opts.append("box=1")
                box_color = t_act.get("box_color") or t_act.get("boxcolor", "0x000000@0.5")
                drawtext_opts.append(f"boxcolor={box_color}")
                box_border_w = int(t_act.get("box_border_width") or t_act.get("boxborderw", 0))
                if box_border_w > 0:
                    drawtext_opts.append(f"boxborderw={box_border_w}")

            shadow_x = int(t_act.get("shadow_x") or t_act.get("shadowx", 0))
            shadow_y = int(t_act.get("shadow_y") or t_act.get("shadowy", 0))
            if shadow_x != 0 or shadow_y != 0:
                drawtext_opts.append(f"shadowx={shadow_x}")
                drawtext_opts.append(f"shadowy={shadow_y}")
                shadow_col = t_act.get("shadow_color") or t_act.get("shadowcolor", "black")
                drawtext_opts.append(f"shadowcolor={shadow_col}")

            next_v_label = f"[v_{idx}]" if idx < len(text_actions) else "[vout]"
            filter_complex_parts.append(f"{current_v_label}drawtext={':'.join(drawtext_opts)}{next_v_label}")
            current_v_label = next_v_label

        mapped_video_label = "[vout]"
    else:
        mapped_video_label = "0:v"

    # Prepare output directory
    abs_output_path = os.path.abspath(output_path)
    output_dir = os.path.dirname(abs_output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    # Build final command with standard optimal production codecs
    cmd = [ffmpeg_exe, "-y"] + input_args

    if filter_complex_parts:
        cmd.extend(["-filter_complex", "; ".join(filter_complex_parts)])

    cmd.extend(["-map", mapped_video_label])

    if mapped_audio_label:
        cmd.extend(["-map", mapped_audio_label])
        cmd.extend(["-c:a", "aac", "-b:a", "320k"])
    else:
        cmd.append("-an")

    cmd.extend([
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-shortest",
        abs_output_path
    ])

    # Execute ffmpeg
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0 or not os.path.exists(abs_output_path) or os.path.getsize(abs_output_path) == 0:
            err_msg = res.stderr.strip() if res.stderr else "Unknown ffmpeg error."
            # Extract last few lines of stderr for concise feedback
            stderr_lines = [l for l in err_msg.splitlines() if l.strip()]
            concise_err = "\n".join(stderr_lines[-10:]) if len(stderr_lines) > 10 else err_msg
            return format_tool_response(
                "remix_video",
                payload="",
                errors=f"Error: ffmpeg execution failed (exit code {res.returncode}):\n{concise_err}"
            )

        return format_tool_response("remix_video", payload=abs_output_path, errors="None")

    except Exception as e:
        return format_tool_response(
            "remix_video",
            payload="",
            errors=f"Error executing remix_video tool: {e}"
        )
