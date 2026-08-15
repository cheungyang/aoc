import os
from typing import List, Optional
from langchain_core.tools import tool
from core.util import format_tool_response

@tool
def video_ocr_validator(
    frame_paths: List[str],
    expected_text: List[str],
    agent_id: Optional[str] = None
) -> str:
    """Uses OCR to validate if expected text is present in the video frames.
    
    Args:
        frame_paths: List of absolute paths to the extracted frame images.
        expected_text: List of text strings expected to be visible in the frames.
        agent_id: Optional ID of the calling agent.
        
    Returns:
        The formatted tool response containing a JSON payload with the validation results.
    """
    if not frame_paths:
        return format_tool_response(
            "video_ocr_validator",
            payload="",
            errors="Error: frame_paths cannot be empty."
        )

    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return format_tool_response(
            "video_ocr_validator",
            payload="",
            errors="Error: pytesseract and Pillow must be installed."
        )

    results = {}
    all_text_found = False

    try:
        for frame_path in frame_paths:
            if not os.path.exists(frame_path):
                continue
            
            # Extract text using OCR
            img = Image.open(frame_path)
            extracted_text = pytesseract.image_to_string(img).lower()
            
            frame_results = {}
            for expected in expected_text:
                expected_lower = expected.lower()
                found = expected_lower in extracted_text
                frame_results[expected] = found
                if found:
                    all_text_found = True
            
            results[frame_path] = {
                "extracted_text": extracted_text.strip(),
                "matches": frame_results
            }

        import json
        payload = json.dumps({
            "all_expected_text_found": all_text_found,
            "frame_details": results
        })

        return format_tool_response(
            "video_ocr_validator",
            payload=payload,
            errors="None"
        )

    except Exception as e:
        return format_tool_response(
            "video_ocr_validator",
            payload="",
            errors=f"Error running OCR: {e}"
        )
