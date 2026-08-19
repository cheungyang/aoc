import datetime
import time
import os
import re
import ast
import json
from typing import Any

def split_message(text, limit=2000):
    """Splits a text into chunks of at most 'limit' characters."""
    if not text:
        return []
        
    chunks = []
    while len(text) > limit:
        # Find the last newline before the limit
        idx = text.rfind('\n', 0, limit)
        if idx == -1:
            # If no newline, find the last space
            idx = text.rfind(' ', 0, limit)
            if idx == -1:
                # If no space, hard split
                idx = limit
        
        chunks.append(text[:idx].strip())
        text = text[idx:].lstrip()
        
    if text:
        chunks.append(text)
    return chunks


def compress_image_bytes(image_bytes: bytes, max_dim: int = 1560, quality: int = 80) -> tuple[bytes, str]:
    """Resizes and compresses image bytes to optimize storage and transmission."""
    if not image_bytes:
        return image_bytes, "image/jpeg"
    try:
        import io
        from PIL import Image
        with Image.open(io.BytesIO(image_bytes)) as img:
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            
            width, height = img.size
            if max(width, height) > max_dim:
                ratio = max_dim / float(max(width, height))
                new_size = (int(width * ratio), int(height * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
                
            output = io.BytesIO()
            img.save(output, format="JPEG", quality=quality, optimize=True)
            return output.getvalue(), "image/jpeg"
    except Exception as e:
        print(f"compress_image_bytes: Warning - failed to compress image: {e}")
        return image_bytes, "image/jpeg"


def get_formatting_prompt():
    return """<formatting_rules>
If you want to present options to the user, use the optional <poll> tag after your response, formatted below.
Option text (<text>) MUST be concise and 80 or fewer characters in length (to meet Discord button limits).
Do not include any xml response to the user except the <poll>, <images>, and <videos> blocks.
<poll allow_multiple="{{true_or_false}}">
    <question>{{question to ask the user}}</question>
    <options>
        <option>
            <text>{{first option (max 80 characters)}}</text>
            <emoji>{{emoji to best represent the option}}</emoji>
            <response>{{text to send when this option is selected}}</response>
        </option>
        {{...additional <option></option> tags for each option...}}
    </options>
</poll>

If you want to send images to the user, use the <images> tag, formatted below.
<images>
    <image path="{{path to the image file}}"/>
    {{...additional <image path="..."/> tags for each image...}}
</images>

If you want to send videos to the user, use the <videos> tag, formatted below.
<videos>
    <video path="{{path to the video file}}"/>
    {{...additional <video path="..."/> tags for each video...}}
</videos>
</formatting_rules>

<tool_execution_rules>
- Permission Restrictions: If a tool use or execution is blocked by permission, do NOT re-attempt or retry the action with different paths or parameter variations, as this will not help and permissions cannot be bypassed by retrying. Instead, explain the limitation to the user or delegate to an authorized specialized agent.
- Cross-Channel Communication: If you intend to send a message to a different Discord channel (via `agent_call` with a `channel` parameter), you MUST obtain the user's explicit approval before doing so.
</tool_execution_rules>"""


def get_channel_prompt(channel_name: str = None) -> str:
    if channel_name is None:
        from core.agent.job_manager import current_channel_name
        channel_name = current_channel_name.get()

    if channel_name:
        return f"<current_channel_context>\nYou are currently executing in the Discord channel: #{channel_name}\n</current_channel_context>"
    return ""


def get_knowledge_prompt():
    now = datetime.datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    day_of_week = now.strftime("%A")
    weekday = now.weekday()
    day_type = "Weekday" if weekday < 5 else "Weekend"
    
    tz_str = time.strftime('%Z')
    if not tz_str:
        tz_str = "UTC-7"
        
    knowledge = [
        f"Today's Date: {date_str}",
        f"Today is: {day_of_week} ({day_type})",
        f"Current Time: {time_str}",
        f"Current Timezone: {tz_str}",
        "Current City: San Jose"
    ]

    return "<common_knowledge>\n" + "\n".join([f"- {k}" for k in knowledge]) + "\n</common_knowledge>"


def format_tool_response(tool_name: str, payload: str, errors: str = "None") -> str:
    return f"""<{tool_name}_response>
  <payload>{payload}</payload>
  <errors>{errors}</errors>
</{tool_name}_response>"""


def format_error_message(error: Any) -> str:
    """Formats an exception or error payload into a clean, informative error message."""
    if not error:
        return "Sorry, I encountered an error processing the request."

    candidate_errors = [error]
    if isinstance(error, BaseException):
        if error.__cause__:
            candidate_errors.append(error.__cause__)
        if error.__context__:
            candidate_errors.append(error.__context__)

    for err in candidate_errors:
        error_str = str(err).strip()
        if not error_str or error_str == "None":
            continue

        code = None
        message = None

        if hasattr(err, "code") and getattr(err, "code"):
            code = getattr(err, "code")
        if hasattr(err, "status_code") and getattr(err, "status_code"):
            code = getattr(err, "status_code")
        if hasattr(err, "message") and getattr(err, "message") and isinstance(getattr(err, "message"), str):
            message = getattr(err, "message")

        # Try to parse dict/json from error_str
        dict_match = re.search(r"(\{.*\})", error_str, re.DOTALL)
        if dict_match:
            dict_str = dict_match.group(1)
            parsed_dict = None
            try:
                parsed_dict = json.loads(dict_str)
            except Exception:
                try:
                    parsed_dict = ast.literal_eval(dict_str)
                except Exception:
                    pass

            if isinstance(parsed_dict, dict):
                err_obj = parsed_dict.get("error", parsed_dict)
                if isinstance(err_obj, dict):
                    if not code:
                        code = err_obj.get("code") or err_obj.get("status_code")
                    if not message:
                        message = err_obj.get("message")
                elif isinstance(err_obj, str) and not message:
                    message = err_obj

        if not code:
            code_match = re.search(r"\b([45]\d{2})\b", error_str)
            if code_match:
                code = code_match.group(1)

        if code and message:
            return f"[{code}] {message}"
        elif code and not message:
            cleaned = re.sub(r"^(?:Error code:\s*)?" + re.escape(str(code)) + r"(?:\s+[A-Z_]+(?:\.|\:|\b))?\s*", "", error_str).strip()
            if dict_match and dict_match.group(1) in cleaned:
                cleaned = cleaned.replace(dict_match.group(1), "").strip().rstrip(".-: ")
            if cleaned:
                return f"[{code}] {cleaned}"
            return f"[{code}] Error processing request."
        elif message:
            return message
        elif error_str:
            return error_str

    return "Sorry, I encountered an error processing the request."


def _load_prompt_from_file(file_inputs, tag, group_desc=None):
    combined_content = []
    for file_path, desc in file_inputs:
        if os.path.exists(file_path):
            file_name = os.path.basename(file_path)
            with open(file_path, "r") as f:
                content = f.read()
            
            # Strip filename row (e.g. # CONTEXT.md) and subsequent empty rows
            lines = content.splitlines()
            if lines and lines[0].strip() == f"# {file_name}":
                lines = lines[1:]
                while lines and not lines[0].strip():
                    lines = lines[1:]
                content = "\n".join(lines)

            combined_content.append(content)
    
    if combined_content:
        content = "\n\n".join(combined_content)
        if group_desc:
            return f"<{tag}>\n<description>{group_desc}</description>\n<content>{content}</content>\n</{tag}>"
        else:
            return f"<{tag}>\n<content>{content}</content>\n</{tag}>"
    return ""


def get_agent_prompt(agent_id):
    agents_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "agents"))
    agent_path = os.path.join(agents_dir, agent_id)
    pkm_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "pkm", "agents", agent_id))

    files = {
        "AGENT": (os.path.join(agent_path, "AGENTS.md"), "Your specialization and workflow:"),
        "INSTRUCTIONS": (os.path.join(agent_path, "INSTRUCTIONS.md"), "Your instructions and workflow:"),
        "IDENTITY": (os.path.join(agent_path, "IDENTITY.md"), "Short description of who you are:"),
        "SOUL": (os.path.join(agent_path, "SOUL.md"), "Your personality, behavior and guiding success in your tasks:"),
        "USER": (os.path.join(agent_path, "USER.md"), "Information about your human:"),
        "MEMORY": (os.path.join(pkm_dir, "MEMORY.md"), "Long term memory on key decisions and learnings to make your tasks successful:"),
        "CONTEXT": (os.path.join(pkm_dir, "CONTEXT.md"), "Context about your human to improve personalization:"),
        "FEEDBACK": (os.path.join(pkm_dir, "FEEDBACK.md"), "Feedbacks from human to adhere to, avoid repeating the same mistake:")
    }
    
    prompt_parts = [
        _load_prompt_from_file([files["AGENT"], files["INSTRUCTIONS"]], "SYSTEM_PURPOSE", "Your purpose, specialization and workflow"),
        _load_prompt_from_file([files["IDENTITY"], files["SOUL"]], "PERSONA", "This is who you are and how you behave"),
        _load_prompt_from_file([files["USER"], files["CONTEXT"]], "HUMAN_CONTEXT", "Information about your human"),
        _load_prompt_from_file([files["MEMORY"]], "MEMORY_AND_PRECEDENTS", "Long term memory on key decisions and learnings to make your tasks successful."),
        _load_prompt_from_file([files["FEEDBACK"]], "FEEDBACK_TO_ADHERE_TO", "Feedbacks from human that you MUST adhere.")
    ]

    return "\n\n".join(prompt_parts) if prompt_parts else ""
