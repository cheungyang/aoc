"""Presence: "hello?", "are you there?", "can you hear me?" -- you checking the
room hears you, which on joining means you haven't heard its greeting.

The greeting can be lost on joining: your client hears nothing until it has
connected to the voice server and joined the call's end-to-end encryption, and
a phone's audio route may open later still (core/channel/voice/room/connection.py
`until_audible`). In the prod logs a session opened on "Hello." with nothing
playing, and the lobby answered it with "Who's it for?". So a presence check
said before you've done anything else in the session -- no switch, no turn
with an agent -- gets the greeting again; one said while the greeting is still
on its way is answered by the greeting itself. Said later, it's words for the
agent you're with, as before ("Hello?" to Daisy is for Daisy).

Said over the greeting while it plays, these words don't stop it either: they
are what you say when you're not sure you're heard, not a request
(core/channel/voice/listening/turn_taking.py `keeps_greeting`).
"""
import re
from typing import Optional

from core.runtime.commands.base import SpokenCommand, SpokenContext
from core.runtime.commands.spoken.words import clean
from core.runtime.observability.turn_trace import Outcome

# The whole utterance: a greeting or two ("hello", "hi there", "hello hello"),
# a question whether anyone's there or hears you, or both. A name anywhere in
# it makes it something else ("hey Daisy" is a switch), so none is allowed. A
# filler only leads into one ("um, hello?"): a lone "okay" is you answering.
_PRESENCE = re.compile(
    r"^(?:(?:um|uh|ok|okay|so)\s+)*"
    r"(?:(?:hello|hallo|hi|hey|yo)(?: there)?\s*)*"
    r"(?:(?:(?:are|is) (?:you|anyone|anybody|someone|somebody|it) (?:there|here|on|around|listening|working))|"
    r"(?:(?:can|could|do) you hear me(?: now| ok| okay)?)|"
    r"(?:(?:anyone|anybody|someone|somebody|you) (?:there|here|around))|"
    r"(?:testing(?: testing)*))?$"
)


def is_presence_check(transcript: str) -> bool:
    """Whether `transcript` only checks that you're heard: "hello?", "are you
    there?", "can you hear me?"."""
    text = clean(transcript)
    return bool(text) and bool(_PRESENCE.match(text))


class PresenceCommand(SpokenCommand):
    name = "hello"
    summary = "Before anything else in a voice session, says the room's greeting again."
    examples = ("hello?", "are you there?", "can you hear me?")
    outcome = Outcome.GREETING

    def match(self, transcript: str) -> Optional[str]:
        return "" if is_presence_check(transcript) else None

    async def run(self, ctx: SpokenContext, args: str) -> bool:
        return await ctx.voice.presence(ctx.say)
