import contextvars
import os
from contextlib import contextmanager
from typing import Optional

from core.util.time_util import format_timezone_label, get_local_now


def get_formatting_prompt() -> str:
    return """<formatting_rules>
If you want to present options to the user, use the optional <poll> tag after your response, formatted below.
Option text (<text>) MUST be concise and 80 or fewer characters in length (to meet Discord button limits).
Do not include any xml response to the user except <poll>, <images>, <videos>, and <system_memory_log> blocks, and <speech> when <voice_mode> asks for it.
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


# The <speech> rules every voice block shares: live voice turns, scheduled
# posts, and the speech writer's conversions (core/inbox/speech_writer.py).
SPEECH_RULES = """- Answer in the first sentence.
- Condense the wording, never the information: keep every fact, number, name, date, and item
  from your reply. Drop formatting, repetition, and filler.
- Speak lists in one compact pass, not as a numbered readout.
- Leave out only things that aren't spoken information (links, IDs, file paths, code, images),
  and say they're in the channel.
- If it's too long to listen to comfortably (more than about 8 items), give the total, read the
  first items, say how many remain, and ask whether to continue. Never drop items silently.
- Say numbers, times, and units as a person would.
- Use only English words: the voice can't pronounce other scripts. Replace anything written in another
  script with its English name, without the original.
- If you offer choices, ask them as a short question; the user will answer by voice."""

# On a turn the user will hear, appended after the channel block (see
# `get_voice_mode_prompt`). The voice pipeline speaks the <speech> block and
# posts everything after it to the text channel (core/voice/speech/reply.py).
VOICE_MODE_PROMPT = """<voice_mode>
This turn arrived by voice. The user is listening; your full reply also appears in the text channel.

Begin each response with a <speech>...</speech> block, then write your normal text reply.

In <speech>:
""" + SPEECH_RULES + """
- Before calling tools, write a one-sentence <speech> block saying what you're checking.

After </speech>, follow your usual formatting rules. The text reply stays complete.
</voice_mode>"""

# Marks the question a post waits on, so the voice can replay exactly that
# question later. Only posts that may be heard later need it.
QUESTION_RULE = """- Mark a direct question with <question>...</question>, where it stands in <speech>, without repeating it.
  A direct question is one you can't continue without the user's answer, such as "How's your energy, one
  to ten?" or "Should I merge the two notes?". Don't mark an invitation the user may take up or ignore,
  such as "Want to go with it, or swap for a walk?", a poll, a proposal, options, or an agenda."""

# On a scheduled run whose post may be heard later (see
# `scheduled_speech_scope`). The <speech> block is stored with the post's
# channel inbox row (core/inbox) and never posted; the channel gets the rest.
SCHEDULED_VOICE_MODE_PROMPT = """<voice_mode>
This is a scheduled run. Your reply is posted to the text channel; the user may hear it later, by voice, when they switch to you.
It plays right after a greeting that says who's speaking, so don't introduce yourself.

Begin your final response with a <speech>...</speech> block, then write your normal text reply.

In <speech>:
""" + SPEECH_RULES + """
""" + QUESTION_RULE + """

After </speech>, follow your usual formatting rules. The text reply stays complete.
</voice_mode>"""

# The agent whose scheduled run writes a speech version of its post. Set by
# the schedule runner (unless the schedule's priority is IGNORE) around the run.
_scheduled_speech_agent = contextvars.ContextVar("scheduled_speech_agent", default=None)


@contextmanager
def scheduled_speech_scope(agent_id: str):
    """Within this block, `agent_id`'s scheduled run gets the scheduled
    <voice_mode> block. Agents it calls don't: what they write goes back to
    it, not to the channel."""
    token = _scheduled_speech_agent.set(agent_id)
    try:
        yield
    finally:
        _scheduled_speech_agent.reset(token)


def get_voice_mode_prompt() -> str:
    """The <voice_mode> block, on turns the user will hear.

    That is a voice-surface turn of an agent that answers the user: the room's
    agent, or one it hands the turn to (which inherits the voice surface).
    Internal workers -- stateless agents such as graph-worker-low -- inherit it
    too, but what they write goes back to their caller, not to the speaker, so
    they never get the block.

    A scheduled run gets the scheduled variant inside `scheduled_speech_scope`:
    its post may be heard later, from the channel inbox.
    """
    from core.runtime.execution_context import try_context
    sess = try_context()
    if sess is None or sess.is_stateless():
        return ""
    surface = sess.get_surface()
    if surface == "voice":
        return VOICE_MODE_PROMPT
    if surface == "scheduled" and sess.agent_id and sess.agent_id == _scheduled_speech_agent.get():
        return SCHEDULED_VOICE_MODE_PROMPT
    return ""


def get_knowledge_prompt() -> str:
    """Date facts for the system prompt. Changes once a day, so it caches well.

    The time of day is not here: it would change the system prompt every
    minute and with it the cached prefix, history included. Each user message
    carries it instead (`stamp_user_message`).
    """
    now = get_local_now()
    date_str = now.strftime("%Y-%m-%d")
    day_of_week = now.strftime("%A")
    weekday = now.weekday()
    day_type = "Weekday" if weekday < 5 else "Weekend"
    iso_year, iso_week, _ = now.isocalendar()

    knowledge = [
        f"Today's Date: {date_str}",
        f"Today is: {day_of_week} ({day_type}), ISO week {iso_year}-W{iso_week:02d}",
        f"Current Timezone: {format_timezone_label(now)}",
        "All dates and times you state or receive are in this timezone "
        "unless explicitly labeled otherwise. Convert timestamps coming "
        "from tools/APIs (commonly UTC) into it before reporting them.",
        # A fixed example: this block must not change during the day.
        "Each user message starts with the local time it was sent, like [Mon 09:05]. "
        "Never write such a stamp yourself.",
    ]

    return "<common_knowledge>\n" + "\n".join([f"- {k}" for k in knowledge]) + "\n</common_knowledge>"


def time_stamp(now=None) -> str:
    """The local-time stamp put in front of user messages, e.g. "[Fri 06:42]"."""
    now = now or get_local_now()
    return f"[{now.strftime('%a %H:%M')}]"


def stamp_user_message(content):
    """`content` with the current local time in front, for the model.

    Text gets a "[Fri 06:42] " prefix; multimodal content (a list of parts)
    gets the stamp as a leading text part. The stamp is stored with the
    message, so history also shows when each message was sent.
    """
    stamp = time_stamp()
    if isinstance(content, str):
        return f"{stamp} {content}"
    if isinstance(content, list):
        return [{"type": "text", "text": stamp}, *content]
    return content


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


# Every agent's prompt carries these (`get_tool_rules_prompt`). Tools built on
# core.tooling report `partial` or `failed` with the issues; the gap line in
# the memory log is how a tool's limits reach agent-designer.
TOOL_RULES = (
    "- When a tool says partial or failed, tell the user in one line what's missing. "
    "Don't fill the gap with a guess. Offer the fix.",
    "- When a tool falls short, note it in your memory log: "
    "`[MEMORY] Tool gap: <tool>, <file>: <what was missing or asked for>.`",
)


def get_tool_rules_prompt(agent_id: str) -> str:
    """How to treat what tools return, for every agent; static.

    An agent with tools of its own also gets pointers to its WORKFLOW.md and
    RULES.md, once it has them. Only `agents/<agent_id>/` is read, so the block
    changes on deploy, never mid-day.
    """
    agent_dir = os.path.dirname(_agent_prompt_files(agent_id)["AGENT"][0])
    lines = list(TOOL_RULES)
    if os.path.isfile(os.path.join(agent_dir, "WORKFLOW.md")):
        lines.append(
            f"- How your own tools work, rule by rule with worked examples: agents/{agent_id}/WORKFLOW.md. "
            "Read it when the user asks how a number was reached."
        )
    if os.path.isfile(os.path.join(agent_dir, "lib", "rules.py")):
        lines.append(
            f"- The thresholds your tools use are in pkm/agents/{agent_id}/RULES.md. To change one, say the old "
            "and new value, and edit that line once the user agrees."
        )
    return _block("TOOL_RULES", "\n".join(lines), "How to treat what tools return")


def user_path(agent_id: str) -> str:
    """`<pkm>/agents/<agent_id>/USER.md`: the agent's one USER.md, about its human. It never enters the repo."""
    from core.util.config import Config

    return os.path.join(Config().pkm_dir, "agents", agent_id, "USER.md")


def get_agent_memory_prompt(agent_id: str, config: Optional[dict] = None) -> str:
    """The agent's mutable memory (Memory v2), most stable first.

    HUMAN_CONTEXT is the agent's USER.md, in the PKM (`user_path`: facts
    about the user never enter the repo), plus the shared Profile; SHARED_MEMORY holds the
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
    shared = not config.get("stateless")

    user = _read_prompt_file(user_path(agent_id))
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
