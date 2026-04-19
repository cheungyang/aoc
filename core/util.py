import discord
import datetime
import time
import os

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


def get_formatting_prompt():
    return """<formatting_rules>
If you want to present options to the user, use the optional <poll> tag after your response, formatted below:
<poll allow_multiple="{{true_or_false}}">
    <question>{{question to ask the user}}</question>
    <options>
        <option>
            <text>{{first option}}</text>
            <emoji>{{emoji to best represent the option}}</emoji>
            <response>{{text to send when this option is selected}}</response>
        </option>
        {{...additional <option></option> tags for each option...}}
    </options>
</poll>
</formatting_rules>"""

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
    agents_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "agents"))
    agent_path = os.path.join(agents_dir, agent_id)
    pkm_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "pkm", "agents", agent_id))

    files = {
        "AGENT": (os.path.join(agent_path, "AGENTS.md"), "Your specialization and workflow:"),
        "IDENTITY": (os.path.join(agent_path, "IDENTITY.md"), "Short description of who you are:"),
        "SOUL": (os.path.join(agent_path, "SOUL.md"), "Your personality, behavior and guiding success in your tasks:"),
        "USER": (os.path.join(agent_path, "USER.md"), "Information about your human:"),
        "MEMORY": (os.path.join(pkm_dir, "MEMORY.md"), "Long term memory on key decisions and learnings to make your tasks successful:"),
        "CONTEXT": (os.path.join(pkm_dir, "CONTEXT.md"), "Context about your human to improve personalization:"),
        "FEEDBACK": (os.path.join(pkm_dir, "FEEDBACK.md"), "Feedbacks from human to adhere to, avoid repeating the same mistake:")
    }
    
    prompt_parts = [
        _load_prompt_from_file([files["AGENT"]], "SYSTEM_PURPOSE", "Your purpose, specialization and workflow"),
        _load_prompt_from_file([files["IDENTITY"], files["SOUL"]], "PERSONA", "This is who you are and how you behave"),
        _load_prompt_from_file([files["USER"], files["CONTEXT"]], "HUMAN_CONTEXT", "Information about your human"),
        _load_prompt_from_file([files["MEMORY"]], "MEMORY_AND_PRECEDENTS", "Long term memory on key decisions and learnings to make your tasks successful."),
        _load_prompt_from_file([files["FEEDBACK"]], "FEEDBACK_TO_ADHERE_TO", "Feedbacks from human that you MUST adhere.")
    ]

    return "\n\n".join(prompt_parts) if prompt_parts else ""
