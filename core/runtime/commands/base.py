"""The pattern every command follows: the Command base class, its three kinds
-- by how a command is given -- and the context each kind runs in.

    TypedCommand    `!name args`, typed to the Discord bot. ChannelCommand
                    answers in a channel the bot answers; VoiceRoomCommand is
                    for the voice room
    SpokenCommand   said in the voice room, recognized by its phrases
    ControlCommand  typed in the ownership control thread
"""
import asyncio
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple

from core.runtime.commands.ports import ControlThread, Say, SpokenTurn, VoiceControls
from core.runtime.foundation.context import ExecutionContext
from core.util.message_util import safe_chunks

# Where a typed command is for, as `!help` groups them.
CHANNEL = "channel"
VOICE_ROOM = "voice room"


@dataclass
class CommandContext:
    """Where a command was given, and by whom. `channel` is where answers go
    (None: there's nowhere to answer)."""

    channel: Any = None
    author: Any = None

    def _sendable(self) -> bool:
        return self.channel is not None and hasattr(self.channel, "send")

    async def send(self, text: str) -> None:
        """Posts `text` as one message."""
        if self._sendable():
            await self.channel.send(text)

    async def reply(self, text: str, pause_s: float = 0.0) -> None:
        """Posts `text` in Discord-sized chunks (safe_chunks), `pause_s` apart."""
        if not self._sendable():
            return
        for i, chunk in enumerate(safe_chunks(text)):
            if i and pause_s:
                await asyncio.sleep(pause_s)
            await self.channel.send(chunk)


@dataclass
class TypedContext(CommandContext):
    """A `!name` command typed to the Discord bot (core/channel/discord/runner.py)."""

    agent_id: str = ""                          # the bot's agent
    session: Optional[ExecutionContext] = None   # the session a message typed there runs on
    answers_here: bool = False                  # this bot answers messages in that channel
    voice: Optional[VoiceControls] = None       # the bot's voice room


@dataclass
class SpokenContext(CommandContext):
    """A command said in the voice room (core/voice/turn.py)."""

    voice: Optional[VoiceControls] = None
    heard: str = ""                     # what was said
    say: Optional[Say] = None           # say(text, agent_id): a line in that agent's voice
    turn: Optional[SpokenTurn] = None   # the turn it was said in
    log: Callable[[str], None] = print


@dataclass
class ControlContext(CommandContext):
    """A command typed in the ownership control thread (core/channel/discord/runner.py)."""

    agent_id: str = ""                          # the bot's agent
    control: Optional[ControlThread] = None


class Command:
    """One command. A subclass, one per file, sets `name` and `summary` (one
    line, for `!help`) and implements `run`; its kind says how it's given."""

    name: str = ""
    aliases: Tuple[str, ...] = ()
    summary: str = ""

    def match(self, text: str) -> Optional[str]:
        """Its arguments if `text` gives this command ("" for none), else None."""
        raise NotImplementedError

    def available(self, ctx: CommandContext) -> bool:
        """Whether this command is answered here and now."""
        return True

    async def run(self, ctx: CommandContext, args: str) -> Optional[bool]:
        """Does the command. A spoken command returns False when the words
        weren't its own after all, and they go to the agent."""
        raise NotImplementedError


# "!name", then whatever follows it.
_TYPED = re.compile(r"!(\S+)(?:\s+(.*))?\Z", re.DOTALL)


class TypedCommand(Command):
    """Typed to the Discord bot as `!name args`, or one of its aliases."""

    usage: str = ""     # its arguments, as `!help` shows them
    section: str = ""   # CHANNEL or VOICE_ROOM

    def match(self, text: str) -> Optional[str]:
        found = _TYPED.match(text or "")
        if not found or found.group(1) not in (self.name, *self.aliases):
            return None
        return (found.group(2) or "").strip()


class ChannelCommand(TypedCommand):
    """Answered in the channel it's typed in, by the bot that answers messages
    there: the one whose agent hosts it, or the one tagged."""

    section = CHANNEL

    def available(self, ctx: TypedContext) -> bool:
        return ctx.answers_here


class VoiceRoomCommand(TypedCommand):
    """For the voice room. With `voice_host_only`, only the bot that hosts the
    room answers, so one message gets one reply."""

    section = VOICE_ROOM
    voice_host_only = False

    def available(self, ctx: TypedContext) -> bool:
        return ctx.voice is not None and (not self.voice_host_only or ctx.voice.hosts_voice())


class SpokenCommand(Command):
    """Said in the voice room: `match` recognizes its phrases in an utterance,
    and `run` returns True if it answered, False to leave the words for the agent."""

    examples: Tuple[str, ...] = ()   # how it's said, for `!help`
    outcome: str = ""                # the turn trace's outcome when it answers


class ControlCommand(Command):
    """Typed in the ownership control thread. Ownership replays the thread
    through `match` to rebuild who holds which agent; `run` answers it live."""

    usage: str = ""   # how it's typed, for `!help`: "[claim <agent>]"
