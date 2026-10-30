"""Ports: what commands act on, declared here so that a command never imports
the adapter providing it (runtime rule 1). Whoever runs a command passes the
real thing in its context: the Discord runner its voice room, the control
thread and the project's scripts, the voice turn itself.
"""
from typing import Any, Awaitable, Callable, List, Optional, Protocol, Sequence, Tuple

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

    async def switch_to_named(self, phrase: str, say: Say, *, asked: bool = True, bare: bool = False,
                              turn: Any = None) -> Optional[str]:
        """Moves to the agent or channel `phrase` names, saying how to come
        back if that leaves a conversation. Only what a switch phrase `asked`
        for ("switch me to Daisy") gets "Do you mean ...?" for a name that
        isn't exact. A `bare` name, with no switch words at all ("Grace."),
        must sound like a voice (`turn`'s newest utterance). Returns the
        turn's outcome when it answered (a switch, a suggestion, noise
        dropped); None when the words are a request for the agent: they name
        no one, or a bare name is the agent you're with."""

    async def go_back(self, say: Say) -> bool:
        """"Go back": returns to where the room was before its last move.
        False: there's nowhere to go back to, or you've moved on since, and
        the words are a request for the agent."""

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


class ScriptRun(Protocol):
    """Everything one run of a script produced."""

    label: str                 # the script and its arguments
    returncode: Optional[int]  # None: it was stopped at its timeout
    stdout: str
    stderr: str
    seconds: float
    timeout: int               # the timeout it ran under, in seconds

    @property
    def timed_out(self) -> bool:
        """It was stopped at its timeout."""

    @property
    def ok(self) -> bool:
        """It exited with 0."""


class Scripts(Protocol):
    """The project's `scripts/`, as `!python` runs them (core/scheduler/script_runner.py)."""

    @property
    def root(self) -> str:
        """The repo root: scripts run from it, and paths they print are from it."""

    def path(self, script: str) -> str:
        """The script's file. ValueError, saying why, for anything but a bare `*.py` name."""

    def writes(self, path: str, args: Sequence[str]) -> str:
        """Why running the script with `args` may change something, or "" when
        it can't: it declares `READ_ONLY = True`, and no argument is a flag it
        lists in `WRITES_WITH`."""

    async def run(self, script: str, args: Sequence[str], timeout: Optional[int] = None) -> ScriptRun:
        """Runs the script and returns what it produced; `timeout` None is the default one."""
