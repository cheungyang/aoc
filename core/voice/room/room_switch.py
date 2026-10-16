"""Room switching by voice: "take me to day planning" moves you to that room.

Matched on the transcript before the turn is routed, with no model call: the
phrase either names a known room or it doesn't, and a turn that isn't a switch
continues untouched. Matching is deliberately strict -- the whole remainder
after the command must be a known room name or alias -- so "go to day planning
and add a task" is an ordinary request, not a move.

The matching is pure functions; `RoomSwitcher` does the Discord side (moving
the member, waiting for the bot to follow, greeting).
"""
import re
from typing import Awaitable, Callable, Dict, Iterable, Optional, Tuple

import discord

from core.loaders.agents_loader import AgentsLoader

# How long a room switch waits for the bot to follow the user into the new room.
ROOM_FOLLOW_TIMEOUT_S = 8.0

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


class RoomSwitcher:
    """Moves the user to the room they ask for, follows them, and greets them."""

    def __init__(self, bot_runner, connection):
        self.bot_runner = bot_runner
        self.agent_id = bot_runner.agent_id
        self.connection = connection

    def aliases(self) -> Dict[str, str]:
        """Alias -> voice room name, for every room this bot follows the user into.

        A room is known by its own name ("day planning") and by the names and
        ids of the agents that answer in its text channel ("Daisy", "day planner").
        """
        loader = AgentsLoader()
        agent = loader.get_agent(self.agent_id)
        rooms = []
        for room in (self.bot_runner.get_hosted_voice_channels(agent) if agent else []):
            text_channel = re.sub(r"[-_]voice$", "", str(room))
            names = []
            for agent_id in loader.eligible_agents_for_channel(text_channel, exclude_agent_id=self.agent_id):
                names += [(loader.get_agent_config(agent_id) or {}).get("name", ""), agent_id]
            rooms.append((str(room), names))
        return build_room_aliases(rooms)

    def greeting(self, room) -> Tuple[str, Optional[str]]:
        """What the room says when you arrive, and whose voice says it: its
        agent's name in its agent's voice, or the room's name."""
        loader = AgentsLoader()
        text_channel = re.sub(r"[-_]voice$", "", room.name)
        agent_id = loader.sole_eligible_agent(text_channel, exclude_agent_id=self.agent_id)
        name = (loader.get_agent_config(agent_id) or {}).get("name") if agent_id else None
        if name:
            return f"{name} here.", agent_id
        return f"You're in {room_label(room.name)}.", None

    async def switch(self, user, transcript: str, say: Callable[..., Awaitable[None]]) -> bool:
        """Moves the user to the room they asked for. True if this was a switch request.

        No model call: the phrase is matched against the known rooms. The bot
        moves the user's own connection (Discord switches their client over),
        then follows them -- the runner's voice-state handler joins any hosted
        room the user enters, and this waits for that, joining directly only
        if it doesn't happen.
        """
        room_name = match_room(transcript, self.aliases())
        if room_name is None:
            return False
        room = self.connection.find_voice_channel(room_name, getattr(user, "guild", None))
        label = room_label(room_name)
        if room is None:
            print(f"[VoiceManager:{self.agent_id}] Room switch: '{room_name}' isn't a voice channel here.")
            await say(f"I can't find the {label} room.")
            return True
        if getattr(self.connection.channel, "id", None) == room.id:
            await say(f"You're already in {label}.")
            return True

        print(f"[VoiceManager:{self.agent_id}] 🚪 Room switch: moving {getattr(user, 'display_name', user)} to '{room.name}'.")
        try:
            await user.move_to(room)
        except discord.Forbidden:
            await say("I can't move you. I need the Move Members permission.")
            return True
        except Exception as e:
            print(f"[VoiceManager:{self.agent_id}] Room switch to '{room.name}' failed: {e}")
            await say(f"I couldn't move you to {label}.")
            return True

        if not await self.connection.wait_for(room, ROOM_FOLLOW_TIMEOUT_S):
            await self.connection.join(str(room.id))
        await say(*self.greeting(room))
        return True
