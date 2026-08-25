import datetime
import os
import re
from typing import Sequence, Optional
from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
    SystemMessage,
)

SUMMARY_PREFIX = "<conversation_summary>"
SUMMARY_SUFFIX = "</conversation_summary>"


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


def build_heuristic_summary(older_messages: Sequence[BaseMessage], previous_summary: str = "") -> str:
    """
    Builds a high-density, deterministic summary of older conversation turns
    without requiring an active LLM call.
    """
    lines = []
    if previous_summary:
        clean_prev = previous_summary.replace(SUMMARY_PREFIX, "").replace(SUMMARY_SUFFIX, "").strip()
        lines.append(f"Prior Context: {clean_prev}")

    user_intents = []
    actions_taken = []
    key_findings = []

    for msg in older_messages:
        if isinstance(msg, HumanMessage):
            c = msg.content if isinstance(msg.content, str) else str(msg.content)
            clean_c = c.strip().replace("\n", " ")
            if clean_c:
                user_intents.append(clean_c[:200])
        elif isinstance(msg, AIMessage):
            if getattr(msg, "tool_calls", None):
                for tc in msg.tool_calls:
                    tc_name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                    if tc_name:
                        actions_taken.append(tc_name)
            c = msg.content if isinstance(msg.content, str) else str(msg.content)
            if c and len(c) > 20 and not getattr(msg, "tool_calls", None):
                key_findings.append(c[:250].strip().replace("\n", " "))
        elif isinstance(msg, ToolMessage):
            name = getattr(msg, "name", "")
            if name and name not in actions_taken:
                actions_taken.append(name)

    summary_parts = []
    if lines:
        summary_parts.extend(lines)

    if user_intents:
        intents_str = " | ".join(user_intents[-4:])  # Keep up to last 4 distinct user requests in older history
        summary_parts.append(f"User Requests / Goals: {intents_str}")

    if actions_taken:
        unique_tools = list(dict.fromkeys(actions_taken))
        summary_parts.append(f"Tools Utilized: {', '.join(unique_tools)}")

    if key_findings:
        findings_str = " | ".join(key_findings[-3:])
        summary_parts.append(f"Key Points / Outcomes: {findings_str}")

    if not summary_parts:
        return "Earlier conversation history condensed."

    return "\n".join(summary_parts)


def save_agent_memory_log(agent_id: str, log_content: str) -> Optional[str]:
    """
    Appends intercepted memory log content to <pkm_dir>/agents/<agent_id>/memory_logs/YYYY-MM-DD.md
    using the filesystem tool.
    """
    if not log_content or not log_content.strip() or not agent_id:
        return None

    try:
        from core.util.config import Config
        from tools.filesystem import filesystem

        pkm_dir = Config().pkm_dir
        now = datetime.datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")
        log_file = os.path.join(pkm_dir, "agents", agent_id, "memory_logs", f"{today_str}.md")

        formatted_lines = []
        for raw_line in log_content.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            # Strip leading list bullets/hyphens
            line_body = re.sub(r"^[-*•]\s*", "", line)
            # Strip placeholder or prior timestamps e.g. [00:00:00], [HH:MM:SS], [12:34:56]
            line_body = re.sub(r"^\[(?:\d{2}:\d{2}:\d{2}|HH:MM:SS|--:--:--)\]\s*", "", line_body, flags=re.IGNORECASE)
            
            formatted_lines.append(f"- [{time_str}] {line_body}")

        if not formatted_lines:
            return None

        payload = "\n".join(formatted_lines) + "\n"

        result = filesystem.invoke({
            "agent_id": agent_id,
            "instructions": [{
                "action": "append",
                "path": log_file,
                "content": payload
            }]
        })

        result_str = str(result)
        errors_match = re.search(r'<errors>(.*?)</errors>', result_str, re.DOTALL)
        if errors_match:
            errors_content = errors_match.group(1).strip()
            if errors_content and errors_content != "None":
                print(f"Error persisting memory log for agent {agent_id}: {errors_content}")
                return None
        elif "Error" in result_str:
            print(f"Error persisting memory log for agent {agent_id}: {result_str}")
            return None

        return log_file
    except Exception as e:
        print(f"Warning: Failed to persist memory log for agent {agent_id}: {e}")
        return None
