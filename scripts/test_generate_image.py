#!/usr/bin/env python3
import asyncio
import os
import sys
import argparse

# Ensure the project root is in the python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.append(project_root)

from dotenv import load_dotenv
load_dotenv()

from tools.generate_image import generate_image

async def main():
    parser = argparse.ArgumentParser(description="Test the generate_image tool.")
    parser.add_argument("prompt", nargs="?", default=None, help="The prompt for the image. If omitted, a default prompt is used.")
    args = parser.parse_args()

    print("=" * 60)
    print("Image Generation Tool Tester")
    print("=" * 60)
    
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("❌ Error: OPENAI_API_KEY not found in environment or .env file.")
        print("Please ensure it is set before running this test.")
        sys.exit(1)

    prompt = args.prompt
    if not prompt:
        prompt = "A serene Japanese zen garden with a cherry blossom tree in full bloom, high quality digital art."

    print(f"Prompt: '{prompt}'")
    print("Generating image via DALL-E 3...")
    
    try:
        # Invoke via LangChain Tool interface to validate arguments and schema
        result = await generate_image.ainvoke({"prompt": prompt})
        
        if isinstance(result, str) and result.startswith("Error"):
            print(f"\n❌ Failed:")
            print(result)
        else:
            print(f"\n✅ Success!")
            print(f"Image saved at: {result}")
            
    except Exception as e:
        print(f"\n❌ Unexpected exception during test: {e}")

if __name__ == "__main__":
    asyncio.run(main())
