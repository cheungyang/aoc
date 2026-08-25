import datetime
import time
import os
from typing import Optional


def get_formatting_prompt() -> str:
    return """<formatting_rules>
If you want to present options to the user, use the optional <poll> tag after your response, formatted below.
Option text (<text>) MUST be concise and 80 or fewer characters in length (to meet Discord button limits).
Do not include any xml response to the user except <poll>, <images>, <videos>, and <system_memory_log> blocks.
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

<memory_logging_rules>
When a task completes, user feedback occurs, or evergreen user context is revealed, append a <system_memory_log> block at the end of your response. The orchestrator intercepts and removes this block before the user sees it. Do not use file tools for memory logging.
Format:
<system_memory_log>
- [HH:MM:SS] [MEMORY] Task: <task summary>. Status: <Success/Failure>. Decisions: <key decisions>.
- [HH:MM:SS] [FEEDBACK] <direct or indirect human feedback/corrections to adhere to>.
- [HH:MM:SS] [CONTEXT] Evergreen: <persistent user context, preferences, or relationships>.
</system_memory_log>
</memory_logging_rules>
</formatting_rules>

<tool_execution_rules>
- Permission Restrictions: If a tool use or execution is blocked by permission, do NOT re-attempt or retry the action with different paths or parameter variations, as this will not help and permissions cannot be bypassed by retrying. Instead, explain the limitation to the user or delegate to an authorized specialized agent.
- Cross-Channel Communication: If you intend to send a message to a different Discord channel (via `agent_call` with a `channel` parameter), you MUST obtain the user's explicit approval before doing so.
</tool_execution_rules>"""


def get_channel_prompt(channel_name: Optional[str] = None) -> str:
    if channel_name is None:
        from core.agent.job_manager import current_channel_name
        channel_name = current_channel_name.get()

    if channel_name:
        return f"<current_channel_context>\nYou are currently executing in the Discord channel: #{channel_name}\n</current_channel_context>"
    return ""


def get_knowledge_prompt() -> str:
    now = datetime.datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    day_of_week = now.strftime("%A")
    weekday = now.weekday()
    day_type = "Weekday" if weekday < 5 else "Weekend"
    
    tz_str = time.strftime('%Z')
    if not tz_str:
        tz_str = "UTC-7"
        
    knowledge = [
        f"Today's Date: {date_str}",
        f"Today is: {day_of_week} ({day_type})",
        f"Current Timezone: {tz_str}",
    ]

    return "<common_knowledge>\n" + "\n".join([f"- {k}" for k in knowledge]) + "\n</common_knowledge>"


def _load_prompt_from_file(file_inputs, tag, group_desc=None) -> str:
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


def get_agent_prompt(agent_id: str) -> str:
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
