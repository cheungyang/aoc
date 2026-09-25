import os
from typing import Optional

from core.util.time_util import format_timezone_label, get_local_now


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
    from core.runtime.execution_context import try_context
    sess = try_context()

    if channel_name is None and sess:
        channel_name = sess.channel_name

    if sess and sess.is_thread() and channel_name:
        thread_name = getattr(sess.channel_obj, "name", "") or sess.discord_thread_id
        if thread_name and thread_name != channel_name:
            return f"<current_channel_context>\nYou are currently executing in Discord thread '{thread_name}' within channel: #{channel_name}\n</current_channel_context>"

    if channel_name:
        return f"<current_channel_context>\nYou are currently executing in the Discord channel: #{channel_name}\n</current_channel_context>"
    return ""


def get_knowledge_prompt() -> str:
    now = get_local_now()
    date_str = now.strftime("%Y-%m-%d")
    day_of_week = now.strftime("%A")
    weekday = now.weekday()
    day_type = "Weekday" if weekday < 5 else "Weekend"

    knowledge = [
        f"Today's Date: {date_str}",
        f"Today is: {day_of_week} ({day_type})",
        f"Current Timezone: {format_timezone_label(now)}",
        "All dates and times you state or receive are in this timezone "
        "unless explicitly labeled otherwise. Convert timestamps coming "
        "from tools/APIs (commonly UTC) into it before reporting them.",
    ]

    return "<common_knowledge>\n" + "\n".join([f"- {k}" for k in knowledge]) + "\n</common_knowledge>"


def _read_prompt_file(file_path: str) -> str:
    if not os.path.exists(file_path):
        return ""
    file_name = os.path.basename(file_path)
    with open(file_path, "r") as f:
        content = f.read()
    # Strip filename row (e.g. # USER.md) and subsequent empty rows
    lines = content.splitlines()
    if lines and lines[0].strip() == f"# {file_name}":
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
        content = "\n".join(lines)
    return content


def _block(tag: str, content: str, group_desc: Optional[str] = None) -> str:
    if not content or not content.strip():
        return ""
    if group_desc:
        return f"<{tag}>\n<description>{group_desc}</description>\n<content>{content}</content>\n</{tag}>"
    return f"<{tag}>\n<content>{content}</content>\n</{tag}>"


def _load_prompt_from_file(file_inputs, tag, group_desc=None) -> str:
    combined_content = [c for c in (_read_prompt_file(path) for path, _ in file_inputs) if c]
    return _block(tag, "\n\n".join(combined_content), group_desc)


def _agent_prompt_files(agent_id: str) -> dict:
    agents_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "agents"))
    agent_path = os.path.join(agents_dir, agent_id)

    return {
        "AGENT": (os.path.join(agent_path, "AGENTS.md"), "Your specialization and workflow:"),
        "INSTRUCTIONS": (os.path.join(agent_path, "INSTRUCTIONS.md"), "Your instructions and workflow:"),
        "IDENTITY": (os.path.join(agent_path, "IDENTITY.md"), "Short description of who you are:"),
        "SOUL": (os.path.join(agent_path, "SOUL.md"), "Your personality, behavior and guiding success in your tasks:"),
        "USER": (os.path.join(agent_path, "USER.md"), "Information about your human:"),
    }


def get_agent_static_prompt(agent_id: str) -> str:
    """The agent's checked-in definition: purpose and persona.

    These files change only on deploy, so this half belongs in the stable
    prefix of the system prompt.
    """
    files = _agent_prompt_files(agent_id)
    prompt_parts = [
        _load_prompt_from_file([files["AGENT"], files["INSTRUCTIONS"]], "SYSTEM_PURPOSE", "Your purpose, specialization and workflow"),
        _load_prompt_from_file([files["IDENTITY"], files["SOUL"]], "PERSONA", "This is who you are and how you behave"),
    ]
    return "\n\n".join(prompt_parts)


def get_agent_memory_prompt(agent_id: str, config: Optional[dict] = None) -> str:
    """The agent's mutable memory (Memory v2), most stable first.

    HUMAN_CONTEXT is USER.md plus the shared Profile; SHARED_MEMORY holds the
    topics the agent subscribes to (`memory_topics`); then its private memory
    and feedback. The dream rewrites these files, so this text must sit at the
    END of the system prompt, after every stable block -- otherwise a memory
    write invalidates the implicit prompt cache for everything behind it.
    Stateless agents get no shared memory: they keep nothing between runs and
    write no memory logs.
    """
    from core.knowledge.memory import inject

    if config is None:
        from core.loaders.agents_loader import AgentsLoader
        config = AgentsLoader().get_agent_config(agent_id) or {}
    files = _agent_prompt_files(agent_id)
    shared = not config.get("stateless")

    user = _read_prompt_file(files["USER"][0])
    profile = inject.render_profile() if shared else ""
    human = "\n\n".join(part for part in (user, profile) if part)
    topics = inject.render_topics(inject.memory_topics(config, agent_id=agent_id)) if shared else ""

    prompt_parts = [
        _block("HUMAN_CONTEXT", human, "Information about your human"),
        _block("SHARED_MEMORY", topics, "Facts about your human that agents have learned, by topic."),
        _block("MEMORY_AND_PRECEDENTS", inject.render_memory(agent_id), "Long term memory on key decisions and learnings to make your tasks successful."),
        _block("FEEDBACK_TO_ADHERE_TO", inject.render_feedback(agent_id), "Feedbacks from human that you MUST adhere."),
    ]
    return "\n\n".join(part for part in prompt_parts if part)
