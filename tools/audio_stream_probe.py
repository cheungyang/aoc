import os
import subprocess
import shutil
from typing import Optional
from langchain_core.tools import tool
from core.util import format_tool_response

def _get_ffprobe_executable() -> Optional[str]:
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            ffprobe_exe = os.path.join(os.path.dirname(exe), 'ffprobe')
            if os.path.exists(ffprobe_exe):
                return ffprobe_exe
    except Exception:
        pass
    return shutil.which("ffprobe")

@tool
def audio_stream_probe(
    video_path: str,
    agent_id: Optional[str] = None
) -> str:
    """Probes a video file for the presence of an audio stream using ffprobe.
    
    Args:
        video_path: Absolute path to the video file.
        agent_id: Optional ID of the calling agent.
        
    Returns:
        The formatted tool response containing 'True' if an audio stream is detected, or 'False' otherwise.
    """
    if not video_path or not str(video_path).strip():
        return format_tool_response(
            "audio_stream_probe",
            payload="",
            errors="Error: video_path cannot be empty."
        )

    if not os.path.exists(video_path):
        return format_tool_response(
            "audio_stream_probe",
            payload="",
            errors=f"Error: Video file not found at '{video_path}'."
        )

    ffprobe_exe = _get_ffprobe_executable()
    if not ffprobe_exe:
        return format_tool_response(
            "audio_stream_probe",
            payload="",
            errors="Error: ffprobe executable not found."
        )

    try:
        cmd = [
            ffprobe_exe,
            "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=codec_type",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        has_audio = bool(res.stdout.strip())
        
        return format_tool_response(
            "audio_stream_probe",
            payload=str(has_audio),
            errors="None"
        )
    except Exception as e:
        return format_tool_response(
            "audio_stream_probe",
            payload="",
            errors=f"Error running ffprobe: {e}"
        )
