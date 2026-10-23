"""Where am I: "where am I", answered from the focus, without a model call."""
import re
from typing import Optional

from core.runtime.commands.base import SpokenCommand, SpokenContext
from core.runtime.commands.spoken.words import clean
from core.runtime.observability.turn_trace import Outcome

# "Where am I?": answered from the focus, without a model call. "Who are you?"
# is not here on purpose: the agent you're with answers it, in its own words,
# as it would in its channel.
_WHERE = re.compile(
    r"^(?:(?:hey|hi|ok|okay|so|um|uh|concierge)[\s,]+)*"
    r"(?:where am i|where are we|which (?:room|channel) am i in|what (?:room|channel) is this)(?: right now| now)?$"
)


def is_where_am_i(transcript: str) -> bool:
    """Whether `transcript` asks where the focus is."""
    return bool(_WHERE.match(clean(transcript)))


class WhereAmICommand(SpokenCommand):
    name = "where am I"
    summary = "Says which agent and channel the voice room is with."
    examples = ("where am I", "which channel am I in")
    outcome = Outcome.FOCUS_SWITCH

    def match(self, transcript: str) -> Optional[str]:
        return "" if is_where_am_i(transcript) else None

    async def run(self, ctx: SpokenContext, args: str) -> bool:
        line, agent_id = ctx.voice.whereabouts()
        await ctx.say(line, agent_id)
        return True
