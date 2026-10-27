"""Team radio on Discord: its thread under #general, its lines, and what you do to them.

The proactive team (core/proactive/radio.py) decides what each line says. This
module:
  - posts the lines in the "📻 team-radio" thread under #general, with ✅/❌;
  - posts the reason poll under a line;
  - hands your reactions and votes back to the listeners the team registers
    (`on_reaction`, `on_poll_vote`). The runner offers every ✅/❌ and every
    poll vote here first.

Only the host that answers for main posts (Ownership): prod, unless a dev
instance claimed main. Each host keeps its own Board, so a line is labeled
where it was posted.
"""
import asyncio
from datetime import timedelta
from typing import Awaitable, Callable, List, Optional, Sequence

import discord

from core.channel.discord.ownership import PROD, Ownership
from core.channel.handoff import channel_named

THREAD_NAME = "📻 team-radio"
HOST_AGENT = "main"
LOBBY = "general"
POLL_HOURS = 24
# A week without a line archives the thread; the next line wakes it.
ARCHIVE_MINUTES = 10080
# Discord's limits on a poll's question and answers.
POLL_QUESTION_CHARS = 300
POLL_ANSWER_CHARS = 55

ReactionListener = Callable[[str, str], Awaitable[bool]]
VoteListener = Callable[[str, int], Awaitable[bool]]
_reaction_listeners: List[ReactionListener] = []
_vote_listeners: List[VoteListener] = []


def on_reaction(listener: ReactionListener) -> None:
    """Registers `listener(message_id, emoji)`; it returns True for a reaction it handled."""
    _reaction_listeners.append(listener)


def on_poll_vote(listener: VoteListener) -> None:
    """Registers `listener(poll_message_id, answer_id)`; it returns True for a vote it handled."""
    _vote_listeners.append(listener)


def clear_listeners() -> None:
    """For tests."""
    _reaction_listeners.clear()
    _vote_listeners.clear()


async def reaction_added(message_id: str, emoji: str) -> bool:
    """Offers a reaction on any message to the listeners; True once one handled it."""
    for listener in list(_reaction_listeners):
        try:
            if await listener(message_id, emoji):
                return True
        except Exception as e:
            print(f"[radio] A reaction listener failed: {type(e).__name__}: {e}")
    return False


async def poll_vote_added(message_id: str, answer_id: int) -> bool:
    """Offers a poll vote to the listeners; True once one handled it."""
    for listener in list(_vote_listeners):
        try:
            if await listener(message_id, answer_id):
                return True
        except Exception as e:
            print(f"[radio] A poll listener failed: {type(e).__name__}: {e}")
    return False


class DiscordRadio:
    """The team's radio channel (core/proactive/radio.RadioChannel) on Discord."""

    def __init__(self, bots=None, ownership: Optional[Ownership] = None):
        self._bots = bots
        self._ownership = ownership
        self._thread = None
        self._lock = asyncio.Lock()

    async def post(self, text: str, reactions: Sequence[str] = ()) -> Optional[str]:
        """Posts `text` as a radio line, with `reactions` added; its message id, or None if not posted here."""
        thread = await self._ready_thread()
        if thread is None:
            return None
        message = await thread.send(text, allowed_mentions=discord.AllowedMentions.none())
        for emoji in reactions:
            try:
                await message.add_reaction(emoji)
            except discord.HTTPException as e:
                print(f"[radio] Could not add {emoji}: {e}")
        return str(message.id)

    async def poll(self, reply_to: Optional[str], question: str, answers: Sequence[str]) -> Optional[str]:
        """Posts a one-choice poll under the line `reply_to`; its message id, or None if not posted here."""
        thread = await self._ready_thread()
        if thread is None:
            return None
        poll = discord.Poll(question=question[:POLL_QUESTION_CHARS], duration=timedelta(hours=POLL_HOURS))
        for answer in answers:
            poll.add_answer(text=answer[:POLL_ANSWER_CHARS])
        reference = thread.get_partial_message(int(reply_to)) if reply_to else None
        message = await thread.send(poll=poll, reference=reference, mention_author=False)
        return str(message.id)

    async def _ready_thread(self):
        if not await self._posts_here():
            return None
        try:
            return await self._thread_for_lines()
        except discord.HTTPException as e:
            print(f"[radio] The team-radio thread is out of reach: {e}")
            return None

    async def _posts_here(self) -> bool:
        ownership = self._ownership or Ownership()
        if not await ownership.wait_ready():
            # The control thread wasn't read: prod posts, as schedules run, rather than lose the line.
            return ownership.role == PROD
        return ownership.is_mine(HOST_AGENT)

    async def _thread_for_lines(self):
        """The team-radio thread, awake: found by name (archived ones too), or made."""
        if self._thread is not None and not getattr(self._thread, "archived", False):
            return self._thread
        async with self._lock:
            # Imported here: the loader imports the runner, which imports this module.
            from core.channel.discord.loader import BotsLoader

            runner = (self._bots or BotsLoader()).get_bot(HOST_AGENT)
            client = getattr(runner, "bot", None)
            if client is None:
                return None
            await client.wait_until_ready()
            for guild in client.guilds:
                lobby = channel_named(guild, LOBBY)
                if lobby is None:
                    continue
                thread = next((t for t in lobby.threads if t.name == THREAD_NAME), None)
                if thread is None:
                    async for old in lobby.archived_threads(limit=None):
                        if old.name == THREAD_NAME:
                            thread = old
                            break
                if thread is None:
                    thread = await lobby.create_thread(name=THREAD_NAME, type=discord.ChannelType.public_thread,
                                                       auto_archive_duration=ARCHIVE_MINUTES)
                elif thread.archived:
                    thread = await thread.edit(archived=False)
                self._thread = thread
                return thread
        return None
