"""Hooks: how a feature built on the voice room joins in, without the voice
core knowing it.

The voice core -- listening, the focus and switching, speaking -- knows
nothing of the features that use it. A feature that needs to join in (the
channel inbox's voice briefings, core/voice/briefing/hook.py) is a
`VoiceHook`, handed to VoiceManager when it's built. VoiceManager calls every
hook at these moments, in order, and a hook overrides only the ones it needs:

    joined(room)                     you joined: the line to greet you with,
                                     or None for the default ("Concierge here.")
    left(room)                       the bot left the voice room
    handle(room, transcript, say)    a finished utterance, before switch
                                     phrases and the agent: True if handled
    arrived(room, focus, speak)      a switch's greeting was said: anything
                                     the hook wants said next, via `speak`
    turn(room, focus)                a turn is about to run in the focus's channel

`room` is what a hook may use of the voice side (`VoiceRoom`). A hook that
raises is logged and skipped; the voice pipeline carries on.
"""
from typing import Awaitable, Callable, Optional, Protocol

from core.voice.room.focus import FocusDirectory, VoiceFocus
from core.voice.room.room_switch import Say  # say(text, agent_id): a line in that agent's voice

# speak(text): a line in the focus agent's voice, which a lone "yes" can answer.
Speak = Callable[[str], Awaitable[None]]


class VoiceRoom(Protocol):
    """The voice side, as hooks see it (VoiceManager)."""

    def directory(self) -> FocusDirectory:
        """Every focus there is."""

    def focus(self) -> VoiceFocus:
        """Where the next turn goes."""

    async def switch_to(self, focus: VoiceFocus, say: Say) -> bool:
        """Moves the focus, as "switch to …" would: greeting, then `arrived` hooks."""

    def say_when_idle(self, line: str) -> None:
        """Says a concierge line once nobody is talking and no turn is running."""

    def someone_listening(self) -> bool:
        """A person, not a bot, is in the voice room."""


class VoiceHook:
    """Every hook point, doing nothing. Subclass and override what you need."""

    def joined(self, room: VoiceRoom) -> Optional[str]:
        return None

    def left(self, room: VoiceRoom) -> None:
        return None

    async def handle(self, room: VoiceRoom, transcript: str, say: Say) -> bool:
        return False

    async def arrived(self, room: VoiceRoom, focus: VoiceFocus, speak: Speak) -> None:
        return None

    def turn(self, room: VoiceRoom, focus: VoiceFocus) -> None:
        return None
