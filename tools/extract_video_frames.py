import os
import json
import subprocess
import shutil
from typing import Union, List, Optional
from langchain_core.tools import tool
from core.util import format_tool_response


def _parse_single_timestamp(ts: Union[float, int, str]) -> float:
    """Parses a single timestamp into seconds as a float."""
    if isinstance(ts, (int, float)):
        sec = float(ts)
        if sec < 0:
            raise ValueError(f"Timestamp cannot be negative: {ts}")
        return sec
    if isinstance(ts, str):
        s = ts.strip().lower()
        if not s:
            raise ValueError("Timestamp string cannot be empty")

        # Strip descriptive units
        if s.endswith("seconds"):
            s = s[:-7].strip()
        elif s.endswith("second"):
            s = s[:-6].strip()
        elif s.endswith("secs"):
            s = s[:-4].strip()
        elif s.endswith("sec"):
            s = s[:-3].strip()
        elif s.endswith("s") and not s.endswith("ms") and len(s) > 1 and (s[-2].isdigit() or s[-2] == '.'):
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


def _parse_all_timestamps(ts_input: Union[float, int, str, List[Union[float, int, str]]]) -> List[float]:
    """Normalizes and parses single or multiple timestamps into a list of float seconds."""
    if ts_input is None:
        raise ValueError("timestamps parameter cannot be None")

    if isinstance(ts_input, (int, float)):
        return [_parse_single_timestamp(ts_input)]

    if isinstance(ts_input, str):
        s = ts_input.strip()
        if not s:
            raise ValueError("timestamps string cannot be empty")

        # Try parsing JSON array string e.g. "[1.0, 2.5, '00:01']"
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    if not parsed:
                        raise ValueError("timestamps array cannot be empty")
                    return [_parse_single_timestamp(item) for item in parsed]
            except json.JSONDecodeError:
                pass

        # Handle comma-separated list e.g. "1.0, 2.5, 00:03"
        if "," in s:
            items = [item.strip() for item in s.split(",") if item.strip()]
            if not items:
                raise ValueError("timestamps list cannot be empty")
            return [_parse_single_timestamp(item) for item in items]

        return [_parse_single_timestamp(s)]

    if isinstance(ts_input, (list, tuple)):
        if len(ts_input) == 0:
            raise ValueError("timestamps list cannot be empty")
        return [_parse_single_timestamp(item) for item in ts_input]

    raise ValueError(f"Unsupported timestamps input type: {type(ts_input)}")


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


def _extract_frame_ffmpeg(ffmpeg_exe: str, video_path: str, timestamp_sec: float, output_path: str) -> bool:
    """Extracts a single frame using ffmpeg CLI."""
    target_dir = os.path.dirname(output_path)
    if target_dir:
        os.makedirs(target_dir, exist_ok=True)

    cmd = [
        ffmpeg_exe,
        "-y",
        "-ss", f"{timestamp_sec:.3f}",
        "-i", video_path,
        "-frames:v", "1",
        "-q:v", "2",
        output_path
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0


def _extract_frames_cv2(video_path: str, timestamps: List[float], target_files: List[str]) -> None:
    """Extracts frames using OpenCV as a fallback backend."""
    import cv2

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise Exception(f"Failed to open video file '{video_path}'. Ensure it is a valid video format.")

    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        for ts, target_file in zip(timestamps, target_files):
            target_frame_idx = int(round(ts * fps))
            if total_frames > 0 and target_frame_idx >= total_frames:
                target_frame_idx = max(0, total_frames - 1)

            cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000.0)
            ret, frame = cap.read()

            if not ret or frame is None:
                cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame_idx)
                ret, frame = cap.read()

            if not ret or frame is None:
                raise Exception(f"Failed to read frame at timestamp {ts:.3f}s.")

            target_dir = os.path.dirname(target_file)
            if target_dir:
                os.makedirs(target_dir, exist_ok=True)

            write_ok = cv2.imwrite(target_file, frame)
            if not write_ok:
                raise Exception(f"Failed to save extracted frame image to '{target_file}'.")
    finally:
        cap.release()


@tool
def extract_video_frames(
    video_path: str,
    timestamps: Union[float, int, str, List[Union[float, int, str]]],
    output_path: Optional[str] = None,
    output_dir: Optional[str] = None,
    image_format: str = "jpg",
    agent_id: Optional[str] = None,
) -> str:
    """Extract keyframe images from a video at one or more specified timestamps.

    This tool reads a video file (such as MP4) and captures frames at exact timestamps,
    saving them as image files.
    - For a single timestamp: saves to 'output_path' (or into 'output_dir' if output_path is omitted).
    - For multiple timestamps: saves all frames into 'output_dir' (or indexed from output_path if output_dir is omitted).

    Args:
        video_path: Local file path to the source video (e.g. .mp4, .mov, .mkv, .avi).
        timestamps: A single timestamp (e.g. 2.5, "00:01:15", "10s") or a list of timestamps (e.g. [0, 1.5, "00:02:30"]).
        output_path: Target image file path when extracting a single frame (e.g. "frames/frame_1.jpg").
        output_dir: Target directory path when extracting multiple frames (e.g. "frames/").
        image_format: Image file format to use when auto-generating file names ('jpg', 'png', 'webp'). Defaults to 'jpg'.
        agent_id: Optional ID of the calling agent.

    Returns:
        The formatted tool response containing the absolute path(s) to the extracted image file(s).
    """
    if not video_path or not str(video_path).strip():
        return format_tool_response(
            "extract_video_frames",
            payload="",
            errors="Error: video_path cannot be empty."
        )

    if not os.path.exists(video_path):
        return format_tool_response(
            "extract_video_frames",
            payload="",
            errors=f"Error: Video file not found at '{video_path}'."
        )

    if not os.path.isfile(video_path):
        return format_tool_response(
            "extract_video_frames",
            payload="",
            errors=f"Error: Path '{video_path}' is not a file."
        )

    if not output_path and not output_dir:
        return format_tool_response(
            "extract_video_frames",
            payload="",
            errors="Error: Either 'output_path' (for single frame) or 'output_dir' (for multiple frames) must be specified."
        )

    try:
        parsed_timestamps = _parse_all_timestamps(timestamps)
    except Exception as e:
        return format_tool_response(
            "extract_video_frames",
            payload="",
            errors=f"Error parsing timestamps: {e}"
        )

    clean_format = image_format.lstrip(".").lower() if image_format else "jpg"
    if clean_format not in ("jpg", "jpeg", "png", "webp", "bmp"):
        clean_format = "jpg"

    is_single = len(parsed_timestamps) == 1
    target_files: List[str] = []

    for i, ts in enumerate(parsed_timestamps):
        if is_single and output_path:
            target_file = os.path.abspath(output_path)
            root, ext = os.path.splitext(target_file)
            if not ext:
                target_file = f"{target_file}.{clean_format}"
        elif output_dir:
            abs_dir = os.path.abspath(output_dir)
            ts_tag = f"{ts:.3f}s".replace(".", "_")
            if is_single:
                video_stem = os.path.splitext(os.path.basename(video_path))[0]
                filename = f"{video_stem}_frame_{ts_tag}.{clean_format}"
            else:
                filename = f"frame_{i+1:03d}_{ts_tag}.{clean_format}"
            target_file = os.path.join(abs_dir, filename)
        elif output_path:
            abs_out = os.path.abspath(output_path)
            dir_name = os.path.dirname(abs_out)
            stem, ext = os.path.splitext(os.path.basename(abs_out))
            ext = ext if ext else f".{clean_format}"
            ts_tag = f"{ts:.3f}s".replace(".", "_")
            filename = f"{stem}_{i+1:03d}_{ts_tag}{ext}"
            target_file = os.path.join(dir_name, filename)
        else:
            return format_tool_response(
                "extract_video_frames",
                payload="",
                errors="Error: No valid output destination determined."
            )
        target_files.append(target_file)

    try:
        ffmpeg_exe = _get_ffmpeg_executable()
        if ffmpeg_exe:
            for ts, target_file in zip(parsed_timestamps, target_files):
                ok = _extract_frame_ffmpeg(ffmpeg_exe, video_path, ts, target_file)
                if not ok:
                    return format_tool_response(
                        "extract_video_frames",
                        payload="",
                        errors=f"Error: Failed to extract frame at timestamp {ts:.3f}s from '{video_path}'."
                    )
        else:
            # Fallback to OpenCV if available
            _extract_frames_cv2(video_path, parsed_timestamps, target_files)

        if len(target_files) == 1:
            payload = target_files[0]
        else:
            payload = "\n".join(target_files)

        return format_tool_response("extract_video_frames", payload=payload, errors="None")

    except Exception as e:
        return format_tool_response(
            "extract_video_frames",
            payload="",
            errors=f"Error extracting video frames: {e}"
        )

