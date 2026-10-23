"""Next agent: "next agent" walks the channels with something waiting for you.
Voice briefings know where that is (core/voice/briefing/hook.py); the room
asks them (VoiceManager.next_agent)."""
import re
from typing import Optional

from core.runtime.commands.base import SpokenCommand, SpokenContext
from core.runtime.commands.spoken.words import normalize
from core.runtime.observability.turn_trace import Outcome

# "Next agent", as a whole utterance: the keyword that walks the unfinished
# channels. It is tried before the switch phrases (SPOKEN's order), so "take
# me to the next agent" never reaches the near-miss model call.
_NEXT_AGENT = re.compile(
    r"^(?:(?:hey|hi|ok|okay|so|um|uh|please|concierge)\s+)*"
    r"(?:(?:go|take me|bring me|move me|switch(?: me)?|move on|skip) (?:on )?(?:to )?)?"
    r"(?:the )?next agent(?: please)?$"
)


def is_next_agent(transcript: str) -> bool:
    """Whether `transcript` is the "next agent" keyword."""
    return bool(_NEXT_AGENT.match(normalize(transcript)))


class NextAgentCommand(SpokenCommand):
    name = "next agent"
    summary = "Goes to the next channel with something waiting for you, or says there's nothing left."
    examples = ("next agent",)
    outcome = Outcome.HOOK

    def match(self, transcript: str) -> Optional[str]:
        return "" if is_next_agent(transcript) else None

    async def run(self, ctx: SpokenContext, args: str) -> bool:
        return await ctx.voice.next_agent(ctx.say)
