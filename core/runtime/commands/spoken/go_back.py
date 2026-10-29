"""Go back: "go back", "take me back", "wrong agent" -- returns the voice room
to where it was before its last move, at once and with no model call.

It undoes a move you didn't mean: a bare name the room took for a switch
("Frankie here. Say 'go back' for Grace."), or a hand-off to the wrong agent.
It answers until you've talked in the new place, and in any case for
GO_BACK_WINDOW_S after the move (core/voice/voice_manager.py `go_back`). After
that, a bare "go back" is more likely meant for the agent (back to its last
options, say), so the room leaves it to the agent; "back to Grace" (switch.py)
still works.
"""
import re
from typing import Optional

from core.runtime.commands.base import SpokenCommand, SpokenContext
from core.runtime.commands.spoken.words import normalize
from core.runtime.observability.turn_trace import Outcome

# "Go back", as a whole utterance, after optional openers ("no, go back").
_GO_BACK = re.compile(
    r"^(?:(?:hey|ok|okay|so|um|uh|oh|no|sorry|oops|concierge)\s+)*"
    r"(?:go back|take me back|bring me back|switch back|back|undo(?: that)?|wrong agent|wrong one|previous agent)"
    r"(?: please)?$"
)


def is_go_back(transcript: str) -> bool:
    """Whether `transcript` asks to undo the room's last move."""
    return bool(_GO_BACK.match(normalize(transcript)))


class GoBackCommand(SpokenCommand):
    name = "go back"
    summary = ("Returns the voice room to where it was before its last move, until you've talked there "
               "or for a minute and a half after the move.")
    examples = ("go back", "wrong agent")
    outcome = Outcome.FOCUS_SWITCH

    def match(self, transcript: str) -> Optional[str]:
        return "" if is_go_back(transcript) else None

    async def run(self, ctx: SpokenContext, args: str) -> bool:
        return await ctx.voice.go_back(ctx.say)
