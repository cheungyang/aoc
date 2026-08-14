import os
import asyncio
import base64
from typing import Optional
import httpx
from langchain_core.tools import tool
from core.util import format_tool_response
from core.util.config import Config

RUNWAY_API_BASE = os.getenv("RUNWAY_API_BASE", "https://api.dev.runwayml.com")
RUNWAY_API_VERSION = os.getenv("RUNWAY_API_VERSION", "2024-11-06")

@tool
async def generate_animation_runway(
    prompt_text: str,
    image_path: str,
    output_path: str,
    model: str = "gen3a_turbo",
    duration: int = 5,
    ratio: str = "1280:768",
    poll_interval: float = 2.0,
    max_wait_seconds: float = 300.0,
    agent_id: Optional[str] = None
) -> str:
    """Animate a static image into a video using Runway Gen-3 based on a motion prompt.

    This tool passes an image file path and a motion prompt to the Runway Gen-3 API,
    asynchronously polls for task completion, downloads the generated video, and saves
    the resulting .mp4 file to the user's specified path.

    Args:
        prompt_text: The motion prompt describing the camera movement and subject animation.
        image_path: The local file path to the starting input image.
        output_path: The destination file path where the generated .mp4 video will be saved.
        model: The Runway model to use ('gen3a_turbo' or 'gen3'). Defaults to 'gen3a_turbo'.
        duration: Duration of the video in seconds (5 or 10). Defaults to 5.
        ratio: Aspect ratio ('1280:768' or '768:1280'). Defaults to '1280:768'.
        poll_interval: Seconds to wait between polling checks. Defaults to 2.0.
        max_wait_seconds: Maximum total seconds to wait for generation before timeout. Defaults to 300.0.
        agent_id: Optional ID of the calling agent.

    Returns:
        The formatted tool response containing the absolute path to the saved video file.
    """
    api_key = Config().runway_api_key or os.getenv("RUNWAYML_API_SECRET") or os.getenv("RUNWAY_API_KEY")
    if not api_key:
        return format_tool_response(
            "generate_animation_runway",
            payload="",
            errors="Error: RUNWAYML_API_SECRET environment variable not set. Please set it to use this tool."
        )

    if not prompt_text or not prompt_text.strip():
        return format_tool_response(
            "generate_animation_runway",
            payload="",
            errors="Error: prompt_text cannot be empty."
        )

    if not image_path or not os.path.exists(image_path):
        return format_tool_response(
            "generate_animation_runway",
            payload="",
            errors=f"Error: Image file not found at '{image_path}'."
        )

    if not output_path or not output_path.strip():
        return format_tool_response(
            "generate_animation_runway",
            payload="",
            errors="Error: output_path cannot be empty."
        )

    try:
        # Encode image to base64 Data URI
        ext = os.path.splitext(image_path)[1].lower()
        mime_type = "image/jpeg" if ext in [".jpg", ".jpeg"] else "image/png"
        
        def _read_image(path: str) -> str:
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")

        img_b64 = await asyncio.to_thread(_read_image, image_path)
        data_uri = f"data:{mime_type};base64,{img_b64}"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "X-Runway-Version": RUNWAY_API_VERSION,
            "Content-Type": "application/json"
        }

        task_payload = {
            "promptImage": data_uri,
            "promptText": prompt_text,
            "model": model,
            "duration": duration,
            "ratio": ratio
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
            # 1. Create image-to-video task
            create_url = f"{RUNWAY_API_BASE.rstrip('/')}/v1/image_to_video"
            create_res = await client.post(create_url, json=task_payload, headers=headers)
            
            if create_res.status_code not in (200, 201):
                return format_tool_response(
                    "generate_animation_runway",
                    payload="",
                    errors=f"Runway API error ({create_res.status_code}): {create_res.text}"
                )

            task_data = create_res.json()
            task_id = task_data.get("id")
            if not task_id:
                raise Exception(f"Failed to obtain task ID from Runway response: {task_data}")

            # 2. Poll for task completion
            status_url = f"{RUNWAY_API_BASE.rstrip('/')}/v1/tasks/{task_id}"
            elapsed = 0.0
            video_url = None

            while elapsed < max_wait_seconds:
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

                poll_res = await client.get(status_url, headers=headers)
                if poll_res.status_code != 200:
                    continue

                poll_data = poll_res.json()
                status = poll_data.get("status", "").upper()

                if status == "SUCCEEDED":
                    output_list = poll_data.get("output", [])
                    if output_list and len(output_list) > 0:
                        video_url = output_list[0]
                    break
                elif status in ("FAILED", "CANCELLED"):
                    failure_msg = poll_data.get("failure", poll_data.get("failureCode", f"Task {status}"))
                    return format_tool_response(
                        "generate_animation_runway",
                        payload="",
                        errors=f"Runway video generation {status.lower()}: {failure_msg}"
                    )

            if not video_url:
                if elapsed >= max_wait_seconds:
                    return format_tool_response(
                        "generate_animation_runway",
                        payload="",
                        errors=f"Timeout waiting for Runway video generation after {max_wait_seconds}s."
                    )
                raise Exception("Task completed with no output video URL.")

            # 3. Download the video file
            video_res = await client.get(video_url)
            video_res.raise_for_status()
            video_bytes = video_res.content

        # 4. Save video locally
        abs_output_path = os.path.abspath(output_path)
        dir_name = os.path.dirname(abs_output_path)
        if dir_name:
            await asyncio.to_thread(os.makedirs, dir_name, exist_ok=True)

        def _write_video(path: str, data: bytes):
            with open(path, "wb") as f:
                f.write(data)

        await asyncio.to_thread(_write_video, abs_output_path, video_bytes)

        return format_tool_response("generate_animation_runway", payload=abs_output_path, errors="None")

    except Exception as e:
        return format_tool_response("generate_animation_runway", payload="", errors=f"Error generating video with Runway: {e}")
