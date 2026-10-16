"""Room switching by voice: "take me to day planning" moves you to that room.

Matched on the transcript before the turn is routed, with no model call: the
phrase either names a known room or it doesn't, and a turn that isn't a switch
continues untouched. Matching is deliberately strict -- the whole remainder
after the command must be a known room name or alias -- so "go to day planning
and add a task" is an ordinary request, not a move.

Pure functions only; VoiceManager owns the Discord side (moving the member,
waiting for the bot to follow, greeting).
"""
import re
from typing import Dict, Iterable, Optional, Tuple

# A command at the start of the utterance, after optional politeness. The
# capture is everything that follows: the room being asked for.
_COMMAND = re.compile(
    r"^(?:(?:hey|hi|ok|okay|so|um|uh|please|concierge)[\s,]+)*"
    r"(?:can you |could you |would you |will you )?(?:please )?"
    r"(?:take me (?:to|into)|bring me (?:to|into)|move me (?:to|into)|switch (?:me )?(?:to|over to)|"
    r"go (?:to|into)|let'?s go (?:to|into)|i want to (?:talk|speak) (?:to|with)|"
    r"let me (?:talk|speak) (?:to|with)|(?:talk|speak) (?:to|with))\s+"
    r"(?P<target>.+)$"
)

# Words around a room name that don't change which room is meant.
_FILLER = re.compile(r"\b(?:the|room|channel|voice|chat|please|now|thanks|thank you)\b")


def _clean(text: str) -> str:
    """Lowercase, punctuation removed except apostrophes, single spaces."""
    text = (text or "").lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9'\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize(text: str) -> str:
    """The comparable form of a room phrase or alias: possessives and apostrophes dropped too."""
    text = re.sub(r"'s\b", "", _clean(text)).replace("'", "")
    return re.sub(r"\s+", " ", text).strip()


def parse_room_request(transcript: str) -> Optional[str]:
    """The normalized room phrase if `transcript` asks to switch rooms, else None."""
    match = _COMMAND.match(_clean(transcript))
    if not match:
        return None
    target = re.sub(r"\s+", " ", _FILLER.sub(" ", normalize(match.group("target")))).strip()
    return target or None


def room_label(voice_channel_name: str) -> str:
    """'day-planning-voice' -> 'day planning'."""
    name = re.sub(r"([-_ ]voice|[-_ ]vc)$", "", (voice_channel_name or "").lower())
    name = re.sub(r"^(voice[-_ ]|vc[-_ ])", "", name)
    return normalize(name.replace("-", " ").replace("_", " "))


def build_room_aliases(rooms: Iterable[Tuple[str, Iterable[str]]]) -> Dict[str, str]:
    """Alias -> voice channel name.

    `rooms` pairs each voice channel name with extra names for it: the names
    and ids of the agents that answer there ("Daisy", "day-planner"). The room's
    own label ("day planning") is always an alias. An alias claimed by two
    rooms is dropped rather than guessed.
    """
    aliases: Dict[str, str] = {}
    ambiguous = set()
    for channel_name, extra_names in rooms:
        for alias in {room_label(channel_name), *(normalize(n.replace("-", " ")) for n in extra_names)}:
            if not alias:
                continue
            if alias in aliases and aliases[alias] != channel_name:
                ambiguous.add(alias)
            aliases[alias] = channel_name
    for alias in ambiguous:
        del aliases[alias]
    return aliases


def match_room(transcript: str, aliases: Dict[str, str]) -> Optional[str]:
    """The voice channel name `transcript` asks to move to, or None."""
    target = parse_room_request(transcript)
    if target is None:
        return None
    return aliases.get(target)
