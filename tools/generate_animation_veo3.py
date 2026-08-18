import os
import asyncio
from typing import Optional
from langchain_core.tools import tool
from core.util import format_tool_response
from core.util.config import Config

@tool
async def generate_animation_veo3(
    prompt_text: str,
    output_path: str,
    image_path: Optional[str] = None,
    model: str = "veo-3.1-generate-preview",
    duration: int = 8,
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
    poll_interval: float = 5.0,
    max_wait_seconds: float = 600.0,
    agent_id: Optional[str] = None
) -> str:
    """Animate a static image into a video or generate a video from text using Google Veo 3 / 3.1.

    This tool sends a video generation request to the Google GenAI Veo 3 API,
    asynchronously polls the long-running operation until complete, downloads the generated video,
    and saves the resulting .mp4 file to the specified output_path.

    Args:
        prompt_text: The prompt describing the animation, cinematic movement, subject actions, or audio/dialogue.
        output_path: The local destination file path where the generated .mp4 video will be saved.
        image_path: Optional local file path to the starting input image for image-to-video animation.
        model: The Veo model variant ('veo-3.1-generate-preview', 'veo-3.1-lite-generate-preview', 'veo-2.0-generate-001'). Defaults to 'veo-3.1-generate-preview'.
        duration: Video duration in seconds (4, 6, or 8). Defaults to 8.
        aspect_ratio: Aspect ratio ('16:9' or '9:16'). Defaults to '16:9'.
        resolution: Output resolution ('720p', '1080p', '4k'). Defaults to '720p'.
        poll_interval: Seconds to wait between polling checks. Defaults to 5.0.
        max_wait_seconds: Maximum total seconds to wait for generation before timeout. Defaults to 600.0.
        agent_id: Optional ID of the calling agent.

    Returns:
        The formatted tool response containing the absolute path to the saved video file.
    """
    api_key = Config().gemini_api_key or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return format_tool_response(
            "generate_animation_veo3",
            payload="",
            errors="Error: GEMINI_API_KEY environment variable not set. Please set it to use this tool."
        )

    if not prompt_text or not prompt_text.strip():
        return format_tool_response(
            "generate_animation_veo3",
            payload="",
            errors="Error: prompt_text cannot be empty."
        )

    if image_path and not os.path.exists(image_path):
        return format_tool_response(
            "generate_animation_veo3",
            payload="",
            errors=f"Error: Image file not found at '{image_path}'."
        )

    if not output_path or not output_path.strip():
        return format_tool_response(
            "generate_animation_veo3",
            payload="",
            errors="Error: output_path cannot be empty."
        )

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)

        # Auto-detect aspect ratio from input image if present and aspect_ratio was not explicitly passed as 9:16
        if image_path and os.path.exists(image_path) and aspect_ratio == "16:9":
            try:
                from PIL import Image
                with Image.open(image_path) as img:
                    w, h = img.size
                    if h > w:
                        aspect_ratio = "9:16"
            except Exception:
                pass

        video_config = types.GenerateVideosConfig(
            aspect_ratio=aspect_ratio,
            duration_seconds=duration,
            resolution=resolution,
            number_of_videos=1
        )

        image_input = None
        if image_path:
            image_input = types.Image.from_file(location=image_path)

        def _start_operation():
            if image_input:
                return client.models.generate_videos(
                    model=model,
                    prompt=prompt_text,
                    image=image_input,
                    config=video_config
                )
            else:
                return client.models.generate_videos(
                    model=model,
                    prompt=prompt_text,
                    config=video_config
                )

        operation = await asyncio.to_thread(_start_operation)

        elapsed = 0.0
        while not operation.done:
            if elapsed >= max_wait_seconds:
                return format_tool_response(
                    "generate_animation_veo3",
                    payload="",
                    errors=f"Timeout waiting for Veo video generation after {max_wait_seconds}s."
                )
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            operation = await asyncio.to_thread(client.operations.get, operation)

        if hasattr(operation, "error") and operation.error:
            return format_tool_response(
                "generate_animation_veo3",
                payload="",
                errors=f"Veo video generation failed: {operation.error}"
            )

        if not operation.response or not getattr(operation.response, "generated_videos", None):
            reasons = []
            if operation.response:
                if getattr(operation.response, "rai_media_filtered_reasons", None):
                    reasons.extend(operation.response.rai_media_filtered_reasons)
                if getattr(operation.response, "rai_media_filtered_count", None):
                    reasons.append(f"Filtered count: {operation.response.rai_media_filtered_count}")
            reason_str = f" Reasons: {', '.join(str(r) for r in reasons)}" if reasons else ""
            raise Exception(f"No generated video found in Veo operation response.{reason_str}")

        generated_video = operation.response.generated_videos[0]
        abs_output_path = os.path.abspath(output_path)
        dir_name = os.path.dirname(abs_output_path)
        if dir_name:
            await asyncio.to_thread(os.makedirs, dir_name, exist_ok=True)

        def _save_video():
            video_bytes = client.files.download(file=generated_video.video)
            with open(abs_output_path, "wb") as f:
                f.write(video_bytes)

        await asyncio.to_thread(_save_video)

        return format_tool_response("generate_animation_veo3", payload=abs_output_path, errors="None")

    except Exception as e:
        return format_tool_response("generate_animation_veo3", payload="", errors=f"Error generating video with Veo: {e}")
