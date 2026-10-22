"""The channel inbox's vocabulary: what a row is, with every category an enum.

The inbox holds at most one row per text channel: the newest agent post there
that you may not have finished with. That's a scheduled push, or a reply that
needs you. Posts and read signals write it (writer.py); voice briefings read it
(core/voice/briefing.py).
"""
import time
from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional

# Every row is skipped, and never mentioned, 24 hours after its post.
TTL_S = 24 * 3600
# A reply that arrives this long after your message has likely missed you.
LATE_REPLY_S = 10 * 60
# How much of a post the speech writer gets to read.
MAX_EXCERPT_CHARS = 6000


class Source(str, Enum):
    """What made the row."""
    SCHEDULE = "SCHEDULE"      # a push: a scheduled run, a script's output, a scan's problem notice
    REPLY = "REPLY"            # a reply to you; a candidate until the speech writer judges it
    LATE_REPLY = "LATE_REPLY"  # a reply that came 10+ minutes after your message


class Priority(str, Enum):
    """How much a push matters when you're in voice. Set per schedule entry."""
    IGNORE = "IGNORE"    # never a row
    LOW = "LOW"          # not in the digest; "next agent" reaches it last
    DEFAULT = "DEFAULT"  # in the digest
    HIGHEST = "HIGHEST"  # in the digest, and announced live while you're in voice

    @classmethod
    def parse(cls, value) -> "Priority":
        """A schedule entry's `priority`: DEFAULT when absent, any letter case."""
        if value is None:
            return cls.DEFAULT
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            try:
                return cls(value.strip().upper())
            except ValueError:
                pass
        raise ValueError(f"unknown priority {value!r} (allowed: {', '.join(p.value for p in cls)})")

    @property
    def rank(self) -> int:
        """Higher is more urgent."""
        return _RANKS[self]


_RANKS = {Priority.IGNORE: 0, Priority.LOW: 1, Priority.DEFAULT: 2, Priority.HIGHEST: 3}


class Status(str, Enum):
    """Where you are with a row."""
    UNREAD = "UNREAD"                  # not heard yet
    AWAITING_REPLY = "AWAITING_REPLY"  # heard; holds a question you haven't answered
    DONE = "DONE"                      # finished
    EXPIRED = "EXPIRED"                # 24 hours old: skipped silently


class Via(str, Enum):
    """How a row was finished."""
    VOICE = "VOICE"
    TYPED = "TYPED"
    REACTION = "REACTION"


UNFINISHED = (Status.UNREAD, Status.AWAITING_REPLY)


@dataclass(frozen=True)
class InboxRow:
    """One channel's newest agent post, and where you are with it."""
    channel: str                       # the text channel's name: the voice focus key
    agent_id: str                      # who answers there
    posted_at: float                   # epoch seconds
    source: Source
    priority: Priority = Priority.DEFAULT
    status: Status = Status.UNREAD
    schedule_id: Optional[str] = None  # for SCHEDULE rows
    label: Optional[str] = None        # how the digest names it: "your morning check-in"
    speech: Optional[str] = None       # the speech version; None until written
    question: Optional[str] = None     # its marked <question>, replayed while AWAITING_REPLY
    excerpt: Optional[str] = None      # the post itself, for the speech writer
    status_at: Optional[float] = None
    status_via: Optional[Via] = None

    def expired(self, now: Optional[float] = None) -> bool:
        if self.status is Status.EXPIRED:
            return True
        return ((time.time() if now is None else now) - self.posted_at) >= TTL_S

    def unfinished(self, now: Optional[float] = None) -> bool:
        """Something left for you: not finished, and not too old to mention."""
        return self.status in UNFINISHED and not self.expired(now)

    @property
    def is_candidate(self) -> bool:
        """A reply not judged yet. It stays only if it asks you something."""
        return self.source is Source.REPLY and self.speech is None

    @property
    def is_reply(self) -> bool:
        return self.source in (Source.REPLY, Source.LATE_REPLY)

    def with_status(self, status: Status, via: Optional[Via] = None, at: Optional[float] = None) -> "InboxRow":
        return replace(self, status=status, status_via=via, status_at=time.time() if at is None else at)


def excerpt_of(text: str) -> str:
    """The part of a post the speech writer reads."""
    body = (text or "").strip()
    if len(body) > MAX_EXCERPT_CHARS:
        body = body[:MAX_EXCERPT_CHARS].rstrip() + " [...]"
    return body
