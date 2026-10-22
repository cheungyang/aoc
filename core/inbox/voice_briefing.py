"""Voice briefings: the channel inbox, said in the voice room.

    the digest      when you join, who has something for you: about 20
                    seconds or less, with no model call. It replaces
                    "Concierge here." when anything is unfinished.
    "next agent"    the next channel with unfinished business, any time
    arriving        after a switch's greeting: the speech version of the
                    agent's unread post, or just the question it's waiting on
    announcements   one line when the pipeline is idle, for what needs you
                    and turned up after you joined

Concierge only ever says a short label about an agent's work; everything else
is said by the agent, in its own voice, after a focus switch. No prompt waits
for a yes or a no: each one says the words to use.

The composing is pure functions over inbox rows and the focus directory.
`VoiceBriefing` holds one drive's state (what you've visited, what's been
announced) and the speech writer, and joins the voice room as a hook
(core/voice/hooks.py): the voice core never imports this module.
"""
import re
import time
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Set, Tuple

from core.inbox.model import InboxRow, Priority, Source, Status, Via
from core.inbox.speech_writer import SpeechWriter
from core.inbox.store import InboxStore
from core.inbox.writer import mark_heard, mark_read, subscribe
from core.voice.hooks import Say, Speak, VoiceHook, VoiceRoom
from core.voice.room.focus import FocusDirectory, VoiceFocus, spoken_label
from core.voice.room.room_switch import normalize

# A switch into a channel whose speech version is still being written waits
# this long for it (usually a second or less).
ARRIVAL_WAIT_S = 8.0
# The digest names at most this many per line, to stay near 20 seconds.
MAX_NAMED = 4

_NUMBER_WORDS = ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"]

# "Next agent", as a whole utterance: the keyword that walks the unfinished
# channels. Hooks are asked before switch phrases, so "take me to the next
# agent" never reaches the near-miss model call.
_NEXT_AGENT = re.compile(
    r"^(?:(?:hey|hi|ok|okay|so|um|uh|please|concierge)\s+)*"
    r"(?:(?:go|take me|bring me|move me|switch(?: me)?|move on|skip) (?:on )?(?:to )?)?"
    r"(?:the )?next agent(?: please)?$"
)

Announce = Callable[[str], None]


@dataclass(frozen=True)
class Entry:
    """An unfinished row, and how it's spoken about."""
    row: InboxRow
    focus: VoiceFocus
    name: str    # how Concierge names whoever has it: "Daisy"
    alias: str   # what you say to get there: "Daisy"


# --- pure: composing what's said ------------------------------------------------------

def is_next_agent(transcript: str) -> bool:
    """Whether `transcript` is the "next agent" keyword."""
    return bool(_NEXT_AGENT.match(normalize(transcript)))


def spoken_name(directory: FocusDirectory, focus: VoiceFocus) -> str:
    """'Daisy'; 'the concierge' in the lobby; 'the software dev channel' where the concierge answers."""
    if focus.agent_id == directory.voice_host:
        return "the concierge" if focus.is_lobby else f"the {focus.label} channel"
    return directory.names.get(focus.agent_id) or spoken_label(focus.agent_id)


def entries_for(rows: Iterable[InboxRow], directory: FocusDirectory, now: Optional[float] = None) -> List[Entry]:
    """The unfinished rows you can switch to, as entries."""
    entries = []
    for row in rows:
        focus = directory.get(row.channel)
        if focus is None or not row.unfinished(now):
            continue
        entries.append(Entry(row, focus, spoken_name(directory, focus), directory.spoken_alias(focus)))
    return entries


def in_digest(row: InboxRow) -> bool:
    """LOW rows aren't in the digest, nor replies not yet spoken: the speech
    writer judges those after you join, and what needs you is announced."""
    return row.priority is not Priority.LOW and not (row.is_reply and row.speech is None)


def ordered(entries: Iterable[Entry], visited: Iterable[str] = (), exclude: Optional[str] = None) -> List[Entry]:
    """Where "next agent" goes, in order: UNREAD rows (questions first, then
    by priority, oldest first; LOW last), then AWAITING_REPLY rows not visited
    since you joined. Never the channel you're in, nor an unjudged reply."""
    visited = set(visited)
    pool = [e for e in entries if not e.row.is_candidate and e.focus.key != exclude]
    unread = sorted(
        (e for e in pool if e.row.status is Status.UNREAD),
        key=lambda e: (e.row.priority is Priority.LOW, e.row.question is None, -e.row.priority.rank, e.row.posted_at),
    )
    awaiting = sorted(
        (e for e in pool if e.row.status is Status.AWAITING_REPLY and e.focus.key not in visited),
        key=lambda e: e.row.posted_at,
    )
    return unread + awaiting


def part_of_day(hour: int) -> str:
    if 4 <= hour < 12:
        return "Morning"
    return "Afternoon" if 12 <= hour < 18 else "Evening"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _distinct(names: Iterable[str]) -> List[str]:
    seen: List[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return seen


def _join(items: List[str], gapped: bool = False) -> str:
    """'A and B', 'A, B, and C'. `gapped` items leave their verb out after
    the first ("Marvin has the scan, and Rio the listings"), so even two get
    the comma: it's the pause where the verb would be."""
    if len(items) <= 1 or (len(items) == 2 and not gapped):
        return " and ".join(items)
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _names(names: List[str]) -> str:
    """'Ted', 'Ted and Daisy', 'Ted, Daisy, and 3 others'."""
    if len(names) > MAX_NAMED:
        rest = len(names) - (MAX_NAMED - 1)
        return _join(names[:MAX_NAMED - 1] + [f"{rest} others"])
    return _join(names)


def compose_digest(entries: List[Entry], hour: int) -> Optional[str]:
    """The digest, or None when nothing's in it:

        {Morning | Afternoon | Evening}. {N} agents have something for you.
        [{A} and {B} are waiting for your answer.]
        [{Agent} has {label}, {Agent} {label}, and {Agent} {label}.]
        Start with {first}? Say 'start with {first}' to begin.
    """
    shown = [e for e in entries if in_digest(e.row)]
    questions = [e for e in shown if e.row.question]
    labelled = sorted((e for e in shown if not e.row.question and e.row.status is Status.UNREAD),
                      key=lambda e: e.row.posted_at, reverse=True)
    if not questions and not labelled:
        return None

    count = len(_distinct(e.name for e in questions + labelled))
    number = _NUMBER_WORDS[count] if count < len(_NUMBER_WORDS) else str(count)
    lines = [f"{part_of_day(hour)}. {number} agent{'s have' if count != 1 else ' has'} something for you."]
    if questions:
        waiting = _distinct(e.name for e in sorted(questions, key=lambda e: e.row.posted_at))
        lines.append(f"{_cap(_names(waiting))} {'is' if len(waiting) == 1 else 'are'} waiting for your answer.")
    if labelled:
        items = [f"{labelled[0].name} has {labelled[0].row.label}"]
        items += [f"{e.name} {e.row.label}" for e in labelled[1:MAX_NAMED]]
        if len(labelled) > MAX_NAMED:
            items.append(f"{len(labelled) - MAX_NAMED} more")
        lines.append(f"{_cap(_join(items, gapped=True))}.")
    included = set(map(id, questions + labelled))
    first = next(e for e in ordered(shown) if id(e) in included)
    lines.append(f"Start with {first.alias}? Say 'start with {first.alias}' to begin.")
    return " ".join(lines)


def nothing_left(entries: Iterable[Entry]) -> str:
    """What "next agent" says when no channel is left."""
    waiting = _distinct(e.name for e in entries if e.row.status is Status.AWAITING_REPLY)
    if not waiting:
        return "That's everything."
    verb = "is" if len(waiting) == 1 else "are"
    return f"That's everything. {_cap(_names(waiting))} {verb} still waiting for your answer."


def should_announce(row: InboxRow, joined_at: Optional[float], now: Optional[float] = None) -> bool:
    """Whether a row that turned up while you're in voice gets a live line:
    a reply that asks you something or came late, once it's spoken; or a push
    that arrived after you joined and asks you something, or is HIGHEST."""
    if joined_at is None or not row.unfinished(now) or row.is_candidate or row.priority is Priority.LOW:
        return False
    if row.is_reply:
        return row.speech is not None and (row.question is not None or row.source is Source.LATE_REPLY)
    return row.posted_at >= joined_at and (row.question is not None or row.priority is Priority.HIGHEST)


def announcement(entry: Entry) -> str:
    """'William has a question for you. Say 'switch to William' to hear it.'"""
    row = entry.row
    if row.question:
        what = "a question for you"
    elif row.source is Source.LATE_REPLY:
        what = "a reply for you"
    else:
        what = row.label or "an update"
    return f"{_cap(entry.name)} has {what}. Say 'switch to {entry.alias}' to hear it."


# --- one drive's state, as a voice room hook -----------------------------------------

class VoiceBriefing(VoiceHook):
    """The inbox for one voice session: a drive starts when you join."""

    def __init__(self, store: Optional[InboxStore] = None, writer: Optional[SpeechWriter] = None,
                 clock: Callable[[], float] = time.time, hour: Optional[Callable[[], int]] = None):
        self.store = store or InboxStore()
        self.writer = writer or SpeechWriter()
        self._clock = clock
        self._hour = hour or _local_hour
        self.joined_at: Optional[float] = None
        self._visited: Set[str] = set()
        self._announced: Set[Tuple[str, float]] = set()
        self._room: Optional[VoiceRoom] = None
        self._unsubscribe: Optional[Callable[[], None]] = None

    # --- the voice room's hooks ------------------------------------------------------

    def joined(self, room: VoiceRoom) -> Optional[str]:
        """A new drive: the digest to greet you with (None: nothing's unfinished).
        Posts without a speech version get one now, and new rows are listened
        for, so what needs you can be announced."""
        directory = room.directory()
        self.begin()
        self._room = room
        if self._unsubscribe is None:
            self._unsubscribe = subscribe(self._on_new_row)
        digest = self.digest(directory)
        self.prepare(directory, announce=room.say_when_idle)
        return digest

    def left(self, room: VoiceRoom) -> None:
        self.end()

    async def handle(self, room: VoiceRoom, transcript: str, say: Say) -> bool:
        """"Next agent": a switch to the next unfinished channel, or "That's everything."."""
        if not is_next_agent(transcript):
            return False
        directory = room.directory()
        target, nothing_left_line = self.next_unfinished(directory, room.focus())
        if target is None:
            await say(nothing_left_line, directory.voice_host)
            return True
        return await room.switch_to(target, say)

    async def arrived(self, room: VoiceRoom, focus: VoiceFocus, speak: Speak) -> None:
        """What the agent left you: its unread post's speech version, or the
        question it's still waiting on. No model call."""
        found = await self.left_for_you(room.directory(), focus)
        if found:
            text, row = found
            await speak(text)
            self.heard(row)

    def turn(self, room: VoiceRoom, focus: VoiceFocus) -> None:
        """You spoke in the channel: whatever its agent left you there is finished."""
        mark_read(focus.channel_name, Via.VOICE)

    def _on_new_row(self, row: InboxRow) -> None:
        """A post landed in the inbox. While you're in voice it's spoken if it
        needs a speech version, and announced if it needs you."""
        room = self._room
        if room is not None and self.active and room.someone_listening():
            self.consider(row, room.directory(), announce=room.say_when_idle)

    # --- the drive -----------------------------------------------------------------

    def begin(self) -> None:
        """You joined: a new drive, with nothing visited or announced yet."""
        self.joined_at = self._clock()
        self._visited.clear()
        self._announced.clear()

    def end(self) -> None:
        self.joined_at = None

    @property
    def active(self) -> bool:
        return self.joined_at is not None

    def entries(self, directory: FocusDirectory) -> List[Entry]:
        try:
            rows = self.store.unfinished(self._clock())
        except Exception as e:
            print(f"[Briefing] Could not read the inbox: {e}")
            return []
        return entries_for(rows, directory, self._clock())

    def digest(self, directory: FocusDirectory) -> Optional[str]:
        return compose_digest(self.entries(directory), self._hour())

    def next_unfinished(self, directory: FocusDirectory,
                        current: Optional[VoiceFocus]) -> Tuple[Optional[VoiceFocus], str]:
        """Where "next agent" goes, or None and what to say instead."""
        entries = self.entries(directory)
        upcoming = ordered(entries, self._visited, exclude=current.key if current else None)
        if upcoming:
            return upcoming[0].focus, ""
        return None, nothing_left(entries)

    async def left_for_you(self, directory: FocusDirectory, focus: VoiceFocus,
                           wait_s: float = ARRIVAL_WAIT_S) -> Optional[Tuple[str, InboxRow]]:
        """What the agent left you in `focus`, to say after the greeting: the
        speech version of an UNREAD row, or the question of an AWAITING_REPLY
        one. Waits for a speech version still being written."""
        self._visited.add(focus.key)
        row = self._get(focus.channel_name)
        if row is None or not row.unfinished(self._clock()):
            return None
        # You're hearing it now: it needs no announcement.
        self._announced.add((row.channel, row.posted_at))
        if row.speech is None:
            if not self.writer.writing(row.channel):
                self.writer.start(row, spoken_name(directory, focus))
            await self.writer.wait(row.channel, wait_s)
            row = self._get(focus.channel_name)
            if row is None or row.speech is None or not row.unfinished(self._clock()):
                return None
        text = row.speech if row.status is Status.UNREAD else row.question
        return (text, row) if text else None

    @staticmethod
    def heard(row: InboxRow) -> None:
        """`row` was just played: AWAITING_REPLY if it asks something, else DONE."""
        mark_heard(row)

    def prepare(self, directory: FocusDirectory, announce: Announce) -> int:
        """You just joined: every unfinished row without a speech version gets
        one now, in parallel, and each one that needs you is announced.
        Returns how many were started."""
        started = 0
        for entry in self.entries(directory):
            if entry.row.speech is None:
                self.writer.start(entry.row, entry.name,
                                  on_done=lambda row, d=directory: self._maybe_announce(row, d, announce))
                started += 1
        return started

    def consider(self, row: InboxRow, directory: FocusDirectory, announce: Announce) -> None:
        """A row that turned up while you're in voice: spoken if it needs a
        speech version, and announced if it needs you."""
        focus = directory.get(row.channel)
        if not self.active or focus is None:
            return
        if row.speech is None and row.unfinished(self._clock()):
            self.writer.start(row, spoken_name(directory, focus),
                              on_done=lambda r, d=directory: self._maybe_announce(r, d, announce))
            return
        self._maybe_announce(row, directory, announce)

    def _maybe_announce(self, row: InboxRow, directory: FocusDirectory, announce: Announce) -> None:
        key = (row.channel, row.posted_at)
        if not self.active or key in self._announced or not should_announce(row, self.joined_at, self._clock()):
            return
        focus = directory.get(row.channel)
        if focus is None:
            return
        self._announced.add(key)
        announce(announcement(Entry(row, focus, spoken_name(directory, focus), directory.spoken_alias(focus))))

    def _get(self, channel: str) -> Optional[InboxRow]:
        try:
            return self.store.get(channel)
        except Exception as e:
            print(f"[Briefing] Could not read #{channel}'s inbox row: {e}")
            return None


def _local_hour() -> int:
    from core.util.time_util import get_local_now
    return get_local_now().hour
