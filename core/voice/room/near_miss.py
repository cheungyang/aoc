"""Which agent a switch phrase most likely means, when it names none exactly.

"Take me to exercise" names no agent or channel, but Frankie "curates and
tracks home workouts". One small model call picks the closest target -- or
none -- from each target's name, channel and description, and the switcher
then asks "Do you mean fitness coach Frankie?" before switching. It also
catches misheard names ("take me to frankly"). Only near misses get here: an
exact name never calls the model.
"""
import asyncio
from typing import Any, Optional

from core.voice.room.focus import FocusDirectory

# A near miss waits on this call before anything is said, so it's kept short;
# on timeout the phrase is treated as an ordinary request.
RESOLVE_TIMEOUT_S = 4.0

PROMPT = """The user, talking by voice, asked to be switched to: "{phrase}"

They can be switched to these (one per line: key | who answers | channel | what they do):
{targets}

Reply with the key of the one the user most likely means, allowing for speech-to-text \
mistakes in names. Reply none if no one fits, or if the phrase is a request rather than \
whom to talk to: a place, a time or something to be done, as in "the airport on Friday" \
or "Daisy and add a task". Reply with the key or none, and nothing else."""


def target_lines(directory: FocusDirectory) -> str:
    """Every switch target, one line each, for the prompt."""
    lines = []
    for key, focus in sorted(directory.targets.items()):
        if focus.agent_id == directory.voice_host:
            who, what = "the concierge", ("routes you to agents, runs jobs" if focus.is_lobby
                                          else f"the {focus.label} channel")
        else:
            who = directory.role(focus)
            what = directory.descriptions.get(focus.agent_id, "")
        lines.append(f"{key} | {who} | #{focus.channel_name} | {what}".rstrip(" |"))
    return "\n".join(lines)


def parse_choice(content: Any, directory: FocusDirectory) -> Optional[str]:
    """The target key in a model reply, if it names a known target; anything else is None."""
    if isinstance(content, list):
        content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    if not isinstance(content, str):
        return None
    choice = content.strip().strip("`'\".").strip().lower()
    return choice if directory.get(choice) is not None else None


async def closest_target(phrase: str, directory: FocusDirectory) -> Optional[str]:
    """The focus key `phrase` most likely means, or None: no target is close,
    or the call failed (the phrase is then just an ordinary request)."""
    from langchain_core.messages import HumanMessage

    from core.runtime.llm_retry import RetryingChatGoogleGenerativeAI
    from core.runtime.session_manager import SessionManager
    from core.runtime.token_usage_handler import TokenUsageHandler
    from core.util.config import Config
    from core.util.models import resolve_model

    prompt = PROMPT.format(phrase=phrase, targets=target_lines(directory))
    try:
        llm = RetryingChatGoogleGenerativeAI(model=resolve_model("FLASH_LITE"), max_retries=Config().llm_max_retries)
        # Booked as the voice host's voice work, like the turn it serves.
        session = SessionManager.get_session(directory.voice_host, source="job", surface="voice", record_memory=False)
        reply = await asyncio.wait_for(
            llm.ainvoke([HumanMessage(content=prompt)], config={"callbacks": [TokenUsageHandler(session)]}),
            RESOLVE_TIMEOUT_S,
        )
    except Exception as e:
        print(f"[FocusSwitcher] Could not find what {phrase!r} means: {type(e).__name__}: {e}")
        return None
    return parse_choice(getattr(reply, "content", None), directory)
