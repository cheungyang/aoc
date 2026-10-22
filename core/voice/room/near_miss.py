"""Which agent a switch phrase most likely means, when it names none exactly.

"Take me to exercise" names no agent or channel, but Frankie "curates and
tracks home workouts". One task for the stateless worker (graph-worker-low)
picks the closest target -- or none -- from each target's name, channel and
description, and the switcher then tells you the words that reach it: "Do you
mean fitness coach Frankie? Say 'switch to Frankie' to get there." It also
catches misheard names ("take me to frankly"). Only near misses get here: an
exact name never calls the model.
"""
import re
from typing import Any, Optional

from core.voice.room.focus import FocusDirectory

# A near miss waits on this call before anything is said, so it's kept short;
# on timeout the phrase is treated as an ordinary request.
RESOLVE_TIMEOUT_S = 4.0

PROMPT = """<playbook>
You match what a user, talking by voice, asked to be switched to against the agents and channels \
they can switch to. Allow for speech-to-text mistakes in names. A phrase that is a request rather \
than whom to talk to matches no one: a place, a time or something to be done, as in "the airport \
on Friday" or "Daisy and add a task".
</playbook>

<current_state>
The user asked to be switched to: "{phrase}"
They can be switched to these (one per line: key | who answers | channel | what they do):
{targets}
</current_state>

<assigned_task>
Output ONLY <payload>KEY</payload>, with the key of the one the user most likely means, \
or <payload>none</payload> if no one fits.
</assigned_task>"""

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
    from core.runtime.delegation import ask_worker

    prompt = PROMPT.format(phrase=phrase, targets=target_lines(directory))
    result = await ask_worker(prompt, channel=directory.lobby.channel_name, caller="near-miss",
                              timeout=RESOLVE_TIMEOUT_S)
    if result is None:
        print(f"[FocusSwitcher] Could not find what {phrase!r} means.")
        return None
    return parse_choice(result.text, directory)
