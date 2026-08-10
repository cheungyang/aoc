import os
import asyncio
from langchain_core.tools import tool
from core.util import format_tool_response
from core.util.config import Config
from PIL import Image
import base64
from io import BytesIO

@tool
async def generate_image(prompt: str, output_path: str, image_base64: str = None, image_path: str = None) -> str:
    """Generate an image using Gemini (Imagen) based on a text prompt.

    This tool sends a generation request to Gemini, extracts the generated image,
    and saves it locally to the specified location.
    It can also accept an input image in base64 format or via a file path to make modifications referenced by the prompt.

    Args:
        prompt: The text description of the image to generate.
        output_path: The file path where the image should be stored.
        image_base64: Optional base64 encoded image to reference for modifications.
        image_path: Optional file path to an image to reference for modifications.

    Returns:
        The absolute path to the saved image file, so it can be used by other tools.
    """
    api_key = Config().gemini_api_key
    if not api_key:
        return format_tool_response("generate_image", payload="", errors="Error: GEMINI_API_KEY environment variable not set. Please set it to use this tool.")

    try:
        # Lazy import to avoid failures if google-genai isn't installed in some contexts
        from google import genai
        
        client = genai.Client(api_key=api_key)
        
        contents = [prompt]
        
        if image_base64:
            try:
                image_data = base64.b64decode(image_base64)
                input_image = Image.open(BytesIO(image_data))
                contents.append(input_image)
            except Exception as e:
                 return format_tool_response("generate_image", payload="", errors=f"Error decoding input image: {e}")
        elif image_path:
            try:
                if not os.path.exists(image_path):
                     return format_tool_response("generate_image", payload="", errors=f"Error: Image file not found at {image_path}")
                input_image = await asyncio.to_thread(Image.open, image_path)
                contents.append(input_image)
            except Exception as e:
                 return format_tool_response("generate_image", payload="", errors=f"Error reading input image file: {e}")

        
        # Generate image with Gemini (run in thread to avoid blocking event loop)
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-3.1-flash-image-preview",
            contents=contents,
        )
        
        image = None
        for part in response.parts:
            if part.inline_data is not None:
                image = part.as_image()
                break
                
        if not image:
             raise Exception("No image data found in response")
             
        output_path = os.path.abspath(output_path)
        # Ensure the directory exists (run in thread to avoid blocking event loop)
        await asyncio.to_thread(os.makedirs, os.path.dirname(output_path), exist_ok=True)
            
        # Save to location (run in thread to avoid blocking event loop)
        await asyncio.to_thread(image.save, output_path)
            
        return format_tool_response("generate_image", payload=output_path, errors="None")
        
    except Exception as e:
        return format_tool_response("generate_image", payload="", errors=f"Error generating image: {e}")

        