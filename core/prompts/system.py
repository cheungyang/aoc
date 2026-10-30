"""The system prompt every agent turn starts with, block by block.

`core/runtime/graph_builder.py` stacks these blocks, most static first, for
prompt caching. Their instruction text is in templates/system/; the agent's
own text comes from agents/<id>/, and its memory from the PKM. The voice block
is in core/prompts/voice.py, and Live Context's in core/prompts/live_context.py.
"""
import json
import os
import re
from dataclasses import dataclass
from typing import Optional

from core.prompts.base import Prompt
from core.util.local_time import format_timezone_label, get_local_now


class FormattingPrompt(Prompt):
    """How replies are formatted (polls, images, videos, memory logs) and how tools are used."""

    template = "system/formatting_rules"


@dataclass
class ChannelPrompt(Prompt):
    """Where the turn runs: a channel, or a thread within one."""

    template = "system/channel"
    channel: str
    thread: str = ""


@dataclass
class KnowledgePrompt(Prompt):
    """Date facts. The example stamp in the template is fixed: this block must
    not change during the day (see `get_knowledge_prompt`)."""

    template = "system/common_knowledge"
    date: str
    day_of_week: str
    day_type: str
    iso_week: str
    timezone: str


@dataclass
class ToolRulesPrompt(Prompt):
    """Every agent's prompt carries these (`get_tool_rules_prompt`). Tools built
    on core.tooling report `partial` or `failed` with the issues; the gap line
    in the memory log is how a tool's limits reach agent-designer."""

    template = "system/tool_rules"
    agent_id: str
    workflow: bool = False    # agents/<id>/WORKFLOW.md exists
    thresholds: bool = False  # agents/<id>/lib/rules.py exists, so pkm/agents/<id>/RULES.md holds its thresholds
    thinking: bool = False    # its model calls take a thinking level, so it may pick its next one


@dataclass
class SkillsListPrompt(Prompt):
    """The agent's skills, one line each (core/loaders/skills_loader.py)."""

    template = "system/skills_list"
    skills: str


@dataclass
class SubgraphsListPrompt(Prompt):
    """The graphs the agent may call, one line each (core/loaders/graphs_loader.py)."""

    template = "system/subgraphs_list"
    graphs: str


def get_formatting_prompt() -> str:
    return FormattingPrompt().render()


def get_channel_prompt(channel_name: Optional[str] = None) -> str:
    from core.runtime.foundation.context import try_context
    sess = try_context()

    if channel_name is None and sess:
        channel_name = sess.channel_name

    if sess and sess.is_thread() and channel_name:
        thread_name = getattr(sess.channel_obj, "name", "") or sess.discord_thread_id
        if thread_name and thread_name != channel_name:
            return ChannelPrompt(channel=channel_name, thread=thread_name).render()

    if channel_name:
        return ChannelPrompt(channel=channel_name).render()
    return ""


def get_knowledge_prompt() -> str:
    """Date facts for the system prompt. Changes once a day, so it caches well.

    The time of day is not here: it would change the system prompt every
    minute and with it the cached prefix, history included. Each user message
    carries it instead (`stamp_user_message`).
    """
    now = get_local_now()
    iso_year, iso_week, _ = now.isocalendar()
    return KnowledgePrompt(
        date=now.strftime("%Y-%m-%d"),
        day_of_week=now.strftime("%A"),
        day_type="Weekday" if now.weekday() < 5 else "Weekend",
        iso_week=f"{iso_year}-W{iso_week:02d}",
        timezone=format_timezone_label(now),
    ).render()


def time_stamp(now=None) -> str:
    """The local-time stamp put in front of user messages, e.g. "[Fri 06:42]"."""
    now = now or get_local_now()
    return f"[{now.strftime('%a %H:%M')}]"


# What `time_stamp` writes, for code that reads a stamped message back: the
# hand-off rule looks for a name at the start of your words, and the stamp
# must not stand in front of it (core/runtime/foundation/addressing.py).
# `%a` is the locale's day abbreviation, so any letters, with an optional dot.
TIME_STAMP = re.compile(r"\[\w+\.? \d{1,2}:\d{2}\]")


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


def get_tool_rules_prompt(agent_id: str) -> str:
    """How to treat what tools return, for every agent; static.

    An agent with tools of its own also gets pointers to its WORKFLOW.md and
    RULES.md, once it has them, and one whose calls take a thinking level is
    offered the `<next_thinking>` marker (core/runtime/model/thinking.py). Only
    `agents/<agent_id>/` is read, so the block changes on deploy, never mid-day.
    """
    agent_dir = os.path.dirname(_agent_prompt_files(agent_id)["AGENT"][0])
    rules = ToolRulesPrompt(
        agent_id=agent_id,
        workflow=os.path.isfile(os.path.join(agent_dir, "WORKFLOW.md")),
        thresholds=os.path.isfile(os.path.join(agent_dir, "lib", "rules.py")),
        thinking=_takes_thinking_level(agent_dir),
    )
    return _block("TOOL_RULES", rules.render(), "How to treat what tools return")


def _takes_thinking_level(agent_dir: str) -> bool:
    """Whether the agent's model calls take a thinking level: it runs on Gemini (the
    default provider) and isn't a stateless worker, whose calls keep the model's default."""
    try:
        with open(os.path.join(agent_dir, "agent.json"), encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, ValueError):
        config = {}
    return config.get("provider", "google") == "google" and not config.get("stateless")


def user_path(agent_id: str) -> str:
    """`<pkm>/agents/<agent_id>/USER.md`: the agent's one USER.md, about its human. It never enters the repo."""
    from core.config.settings import Config

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
