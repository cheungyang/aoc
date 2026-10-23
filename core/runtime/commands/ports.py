"""Ports: what commands act on, declared here so that a command never imports
the adapter providing it (runtime rule 1). Whoever runs a command passes the
real thing in its context: the Discord runner its voice room and the control
thread, the voice turn itself.
"""
from typing import Any, Awaitable, Callable, List, Optional, Protocol, Tuple

# say(text, agent_id): a line said in that agent's voice.
Say = Callable[..., Awaitable[None]]


class VoiceControls(Protocol):
    """The voice room, as commands use it (VoiceManager, core/voice/voice_manager.py)."""

    def hosts_voice(self) -> bool:
        """This bot speaks in the voice room, so it answers the voice host's commands."""

    async def join(self, room: Optional[str] = None, focus_channel: Any = None) -> bool:
        """Joins `room` (by default the configured one), starting in `focus_channel`'s conversation."""

    async def leave(self) -> None:
        """Leaves the voice room."""

    def room(self) -> Any:
        """The voice channel the bot is in, or None."""

    def focus(self) -> Any:
        """Where the next turn goes; it has `is_lobby` and `channel_name`."""

    async def enroll(self, args: str = "", channel: Any = None) -> str:
        """Starts or cancels taking your voiceprint. Returns what to post back."""

    def voiceprint(self, args: str = "") -> str:
        """The voiceprint gate's state and your voiceprint, or a reset. Returns what to post."""

    async def gate(self, args: str, channel: Any) -> None:
        """Posts the gate's review, or its usage, in `channel`."""

    def whereabouts(self) -> Tuple[str, str]:
        """Where the room is, and the agent who says so."""

    async def switch_to_named(self, phrase: str, say: Say) -> bool:
        """Moves to the agent or channel `phrase` names, or suggests a near miss.
        False: it names nothing, and the words are a request for the agent."""

    async def next_agent(self, say: Say) -> bool:
        """Moves to the next channel with something for you. False: no feature offers that."""


class SpokenTurn(Protocol):
    """The voice turn a spoken command was said in (core/voice/turn.py)."""

    def hold(self) -> None:
        """Drops this utterance, but keeps the words said before it for what's said next."""


class ControlThread(Protocol):
    """The ownership control thread, as its commands use it (core/channel/discord/runner.py)."""

    def agent_ids(self) -> List[str]:
        """The agents this process runs a bot for."""

    def elected(self) -> bool:
        """This bot answers for the whole process, so a command gets one reply."""

    async def claim(self) -> None:
        """Claims this bot's agent for this instance. Raises if it can't."""

    def describe(self) -> str:
        """This instance's view of who holds which agent."""

    async def react(self, emoji: str) -> None:
        """Reacts to the command with `emoji`."""
