"""Which agent a switch phrase most likely means, when it names none exactly.

"Take me to exercise" names no agent or channel, but Frankie "curates and
tracks home workouts". One task for the stateless worker (graph-worker-low,
`NearMissPrompt` in core/prompts/voice.py) picks the closest target -- or
none -- from each target's name, channel and description, and the switcher
then tells you the words that reach it: "Do you mean fitness coach Frankie?
Say 'switch to Frankie' to get there." It also catches misheard names ("take
me to frankly"). Only near misses get here: an exact name never calls the model.
"""
import re
from typing import Any, Optional

from core.prompts.voice import NearMissPrompt
from core.voice.room.focus import FocusDirectory

_PAYLOAD = re.compile(r"<payload\s*>(.*?)</payload\s*>", re.DOTALL | re.IGNORECASE)


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
    payload = _PAYLOAD.search(content)
    if payload:
        content = payload.group(1)
    choice = content.strip().strip("`'\".").strip().lower()
    return choice if directory.get(choice) is not None else None


async def closest_target(phrase: str, directory: FocusDirectory) -> Optional[str]:
    """The focus key `phrase` most likely means, or None: no target is close,
    or the worker failed (the phrase is then just an ordinary request)."""
    reply = await NearMissPrompt(phrase=phrase, targets=target_lines(directory)).run(
        channel=directory.lobby.channel_name)
    if reply is None:
        print(f"[FocusSwitcher] Could not find what {phrase!r} means.")
        return None
    return parse_choice(reply, directory)
