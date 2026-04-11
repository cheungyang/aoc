import os
import requests
import uuid
from langchain_core.tools import tool

@tool
async def generate_image(prompt: str, output_path: str = None) -> str:
    """Generate an image using DALL-E 3 based on a text prompt.

    This tool sends a generation request to OpenAI DALL-E 3, downloads the generated image,
    and saves it locally to the specified location or a default directory.

    Args:
        prompt: The text description of the image to generate.
        output_path: Optional file path where the image should be stored. 
                     If not provided, it will be saved to a default 'generated_images' directory.

    Returns:
        The absolute path to the saved image file, so it can be used by other tools.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return "Error: OPENAI_API_KEY environment variable not set. Please set it to use this tool."

    try:
        # Lazy import to avoid failures if openai isn't installed in some contexts
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        
        # Generate image with DALL-E 3
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            quality="standard",
            n=1,
        )
        
        image_url = response.data[0].url
        
        # Download the generated image
        img_response = requests.get(image_url)
        img_response.raise_for_status()
        
        # Determine output path
        if not output_path:
            workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            images_dir = os.path.join(workspace_root, "generated_images")
            os.makedirs(images_dir, exist_ok=True)
            output_path = os.path.join(images_dir, f"{uuid.uuid4()}.png")
        else:
            output_path = os.path.abspath(output_path)
            # Ensure the directory exists
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
        # Save to location
        with open(output_path, 'wb') as f:
            f.write(img_response.content)
            
        return output_path
        
    except Exception as e:
        return f"Error generating image: {e}"
