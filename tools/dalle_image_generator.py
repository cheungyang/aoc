import os
import asyncio
import base64
from typing import Optional
from langchain_core.tools import tool
from core.util import format_tool_response
from core.util.config import Config

@tool
async def dalle_image_generator(
    prompt: str,
    output_path: str,
    size: str = "1024x1024",
    quality: str = "standard",
    style: str = "vivid",
    agent_id: Optional[str] = None
) -> str:
    """Generate an image using OpenAI DALL-E 3 based on a creative text prompt.

    This tool sends a generation request to the DALL-E 3 API, extracts the generated image,
    and saves it locally to the specified output_path.

    Args:
        prompt: The text description of the image to generate.
        output_path: The file path where the image should be saved.
        size: Image resolution (e.g., '1024x1024', '1024x1792', '1792x1024'). Defaults to '1024x1024'.
        quality: Image quality ('standard' or 'hd'). Defaults to 'standard'.
        style: Image style ('vivid' or 'natural'). Defaults to 'vivid'.
        agent_id: Optional ID of the calling agent.

    Returns:
        The formatted tool response containing the absolute path to the saved image file.
    """
    api_key = Config().openai_api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return format_tool_response(
            "dalle_image_generator",
            payload="",
            errors="Error: OPENAI_API_KEY environment variable not set. Please set it to use this tool."
        )

    if not prompt or not prompt.strip():
        return format_tool_response(
            "dalle_image_generator",
            payload="",
            errors="Error: prompt cannot be empty."
        )

    if not output_path or not output_path.strip():
        return format_tool_response(
            "dalle_image_generator",
            payload="",
            errors="Error: output_path cannot be empty."
        )

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key)

        response = await client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size=size,
            quality=quality,
            style=style,
            n=1,
            response_format="b64_json"
        )

        if not response.data or len(response.data) == 0:
            raise Exception("No image data returned from DALL-E API")

        first_data = response.data[0]
        if hasattr(first_data, "b64_json") and first_data.b64_json:
            image_bytes = base64.b64decode(first_data.b64_json)
        elif hasattr(first_data, "url") and first_data.url:
            import httpx
            async with httpx.AsyncClient() as http_client:
                img_res = await http_client.get(first_data.url)
                img_res.raise_for_status()
                image_bytes = img_res.content
        else:
            raise Exception("No usable b64_json or url in image response")

        abs_output_path = os.path.abspath(output_path)
        dir_name = os.path.dirname(abs_output_path)
        if dir_name:
            await asyncio.to_thread(os.makedirs, dir_name, exist_ok=True)

        def _write_file(path: str, data: bytes):
            with open(path, "wb") as f:
                f.write(data)

        await asyncio.to_thread(_write_file, abs_output_path, image_bytes)

        return format_tool_response("dalle_image_generator", payload=abs_output_path, errors="None")

    except Exception as e:
        return format_tool_response("dalle_image_generator", payload="", errors=f"Error generating image with DALL-E: {e}")
