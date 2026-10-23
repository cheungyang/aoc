"""Switch: "switch me to Daisy", "take me to the market scout". It moves the
voice room's focus -- which text channel the next turn belongs to -- and moves
nobody. The room does the moving (core/voice/room/room_switch.py): an exact
agent or channel name switches at once, with no model call; one that names
nothing exactly gets one small model call and "Do you mean ...?".

Matching is deliberately strict: the whole remainder after the command must be
a known name, so "go to day planning and add a task" is not a move, and a
request that only starts like a switch goes to the agent you're with.
"""
import re
from typing import Dict, Optional

from core.runtime.commands.base import SpokenCommand, SpokenContext
from core.runtime.commands.spoken.words import alias_key, clean
from core.runtime.observability.turn_trace import Outcome

# A command at the start of the utterance, after optional politeness. The
# capture is everything that follows: the target being asked for.
_COMMAND = re.compile(
    r"^(?:(?:hey|hi|ok|okay|so|um|uh|please|concierge)[\s,]+)*"
    r"(?:can you |could you |would you |will you )?(?:please )?"
    r"(?:take me (?:to|into)|bring me (?:to|into)|move me (?:to|into)|switch (?:me )?(?:to|over to)|"
    r"go (?:to|into)|let'?s go (?:to|into)|i want to (?:talk|speak) (?:to|with)|"
    r"let me (?:talk|speak) (?:to|with)|(?:talk|speak) (?:to|with)|"
    r"(?:go |take me |bring me |head )?back (?:to|into)|return to|start (?:with|from))\s+"
    r"(?P<target>.+)$"
)


def parse_room_request(transcript: str) -> Optional[str]:
    """The normalized target phrase if `transcript` asks to switch, else None."""
    match = _COMMAND.match(clean(transcript))
    if not match:
        return None
    return alias_key(match.group("target")) or None


def match_target(transcript: str, aliases: Dict[str, str]) -> Optional[str]:
    """The focus key `transcript` asks to switch to by an exact name, or None."""
    target = parse_room_request(transcript)
    if target is None:
        return None
    return aliases.get(target)


class SwitchCommand(SpokenCommand):
    name = "switch"
    summary = "Moves the voice room to an agent or channel; a name it doesn't know gets \u201cDo you mean ...?\u201d."
    examples = ("switch me to Daisy", "take me to the market scout", "back to the concierge")
    outcome = Outcome.FOCUS_SWITCH

    def match(self, transcript: str) -> Optional[str]:
        return parse_room_request(transcript)

    async def run(self, ctx: SpokenContext, phrase: str) -> bool:
        return await ctx.voice.switch_to_named(phrase, ctx.say)
