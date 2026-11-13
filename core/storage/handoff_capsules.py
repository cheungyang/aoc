"""Hand-off capsules in memory.db: what an agent's <switch> or <pass> left a teammate.

An agent's reply can hand you to a teammate, ending with <switch
to="software-planner"/> (core/prompts/templates/system/team.md): the voice room
moves to the teammate's channel, and a typed channel says where to find it.
Nothing of the conversation went along, so the teammate's first turn there
started cold, and "What did Aki tell you?" reached Elephant with nothing to
answer from. Now graphs/main parks a capsule of the conversation when such a
reply ends (core/runtime/history/handoff.py, with the brief the agent wrote
inside the tag), and attaches it to your next message to that teammate in its
home channel.

A reply can also pass something to a teammate and keep you, ending with <pass
to="day-planner">brief</pass>. Its capsule is parked the same way, `via` PASS,
and waits PASS_TTL_S: you stay with the agent that passed it, and often reach
the teammate later in the day. When you're done, the agent hands you over with
a bare <switch>, whose capsule supersedes the pass's; graphs/main carries the
pass's brief into it, and has it wait at least as long (`park`'s `until`).

A capsule is attached once: `take` claims it with one conditional UPDATE, so of
two turns taking at once only one gets it. Only the newest capsule parked for
a teammate and channel counts: `park` marks the older ones still waiting
superseded. One not taken by its `expires_at` has expired and is never
attached; rows older than KEEP_S, taken or not, are dropped the next time one
is parked. What still waits can be read without claiming it: the capsule for
one teammate (`waiting`), every one (`waiting_all`), and the passes an agent
made that haven't reached their teammates yet (`open_passes`, which its prompt
lists: core/prompts/team.py).

Channels are keyed by name, a thread's being its parent channel's, in lower
case. The schema is core/storage/schemas/handoff_capsules.sql.
"""
import time
from dataclasses import dataclass, replace
from typing import List, Optional

from core.config.settings import Config
from core.storage.base import WAL_PRAGMAS, Row, Store

# How long a parked capsule waits for the teammate's first turn: later than
# that, its conversation is no longer what you're coming in with.
TTL_S = 2 * 3600
# How long a pass's capsule waits: you stay with the agent that passed it, and
# often reach the teammate later in the day.
PASS_TTL_S = 24 * 3600
# How long a row is kept at all, for a look back at what was handed over.
KEEP_S = 7 * 86400
# How the capsule came: an agent's <switch> handed you over...
SWITCH = "switch"
# ...or an agent's <pass> left the teammate a brief, and kept you.
PASS = "pass"
# A capsule still waiting: not taken, not replaced by a newer one, not expired.
_WAITING = "consumed_at IS NULL AND superseded_at IS NULL AND expires_at > ?"


@dataclass(frozen=True)
class ParkedCapsule:
    """One parked capsule: every column of handoff_capsules.sql."""
    id: int
    created_at: float                     # epoch seconds
    from_agent: str                       # the agent whose reply handed you over, or passed it on
    from_channel: str                     # the channel that reply was in
    to_agent: str                         # the teammate it's for
    to_channel: str                       # the teammate's home channel, where it's attached
    capsule: str                          # the <handoff_context> block
    expires_at: float                     # never attached after this
    via: str = SWITCH                     # how it came: SWITCH or PASS
    brief: str = ""                       # what the agent wrote inside the tag; "" if nothing
    consumed_at: Optional[float] = None   # when a turn took it; None while it waits
    consumed_session: str = ""            # the session it was attached in
    superseded_at: Optional[float] = None # when a newer one replaced it


def _capsule(record: Row) -> ParkedCapsule:
    return ParkedCapsule(**{column: record[column] for column in record.keys()})


def channel_key(channel: str) -> str:
    """A channel's name as rows key it: "#Software-Dev" is "software-dev"."""
    return str(channel or "").lstrip("#").lower()


class HandoffCapsuleStore(Store):
    schema = ("handoff_capsules",)
    pragmas = WAL_PRAGMAS

    def default_path(self) -> str:
        return Config().memory_db_path

    def park(self, *, from_agent: str, from_channel: str, to_agent: str, to_channel: str, capsule: str,
             brief: str = "", via: str = SWITCH, now: Optional[float] = None, until: Optional[float] = None) -> int:
        """Parks `capsule` for `to_agent`'s next turn in `to_channel`, and returns its id.
        One still waiting there is superseded: the newest hand-over is the one you
        followed.

        It waits PASS_TTL_S if it's a pass's (`via` PASS), TTL_S otherwise, and at
        least `until` (epoch seconds) when given: a switch's capsule that carries a
        waiting pass's brief keeps the pass's time.
        """
        now = time.time() if now is None else now
        to_channel = channel_key(to_channel)
        expires_at = max(now + (PASS_TTL_S if via == PASS else TTL_S), until or 0.0)
        with self.connect() as conn:
            self.execute("DELETE FROM handoff_capsules WHERE created_at < ?", [now - KEEP_S], conn=conn)
            self.execute("UPDATE handoff_capsules SET superseded_at = ? WHERE to_agent = ? AND to_channel = ? "
                         "AND consumed_at IS NULL AND superseded_at IS NULL", [now, to_agent, to_channel], conn=conn)
            return self.insert("handoff_capsules", {
                "created_at": now, "from_agent": from_agent, "from_channel": channel_key(from_channel),
                "to_agent": to_agent, "to_channel": to_channel, "via": via, "brief": brief or "",
                "capsule": capsule, "expires_at": expires_at,
            }, conn=conn)

    def take(self, to_agent: str, to_channel: str, now: Optional[float] = None,
             session_id: str = "") -> Optional[ParkedCapsule]:
        """Claims the newest capsule waiting for `to_agent` in `to_channel` that hasn't
        expired, for `session_id`, and returns it; None if there's none.

        One conditional UPDATE claims it: of two turns taking it at once, only one
        does, and the other gets None.
        """
        now = time.time() if now is None else now
        with self.connect() as conn:
            waiting = self.query("SELECT * FROM handoff_capsules WHERE to_agent = ? AND to_channel = ? "
                                 f"AND {_WAITING} ORDER BY id DESC LIMIT 1",
                                 [to_agent, channel_key(to_channel), now], conn=conn)
            if not waiting:
                return None
            claimed = self.execute("UPDATE handoff_capsules SET consumed_at = ?, consumed_session = ? "
                                   "WHERE id = ? AND consumed_at IS NULL AND superseded_at IS NULL",
                                   [now, session_id or "", waiting[0]["id"]], conn=conn)
        if claimed != 1:
            return None
        return replace(_capsule(waiting[0]), consumed_at=now, consumed_session=session_id or "")

    def waiting(self, to_agent: str, to_channel: str, now: Optional[float] = None) -> Optional[ParkedCapsule]:
        """The capsule waiting for `to_agent`'s next turn in `to_channel` -- the one
        `take` would claim -- without claiming it; None if there's none."""
        now = time.time() if now is None else now
        found = self.query(f"SELECT * FROM handoff_capsules WHERE to_agent = ? AND to_channel = ? AND {_WAITING} "
                           "ORDER BY id DESC LIMIT 1", [to_agent, channel_key(to_channel), now])
        return _capsule(found[0]) if found else None

    def waiting_all(self, now: Optional[float] = None) -> List[ParkedCapsule]:
        """Every capsule still waiting, for any teammate, oldest first; none is claimed."""
        now = time.time() if now is None else now
        return [_capsule(record) for record in
                self.query(f"SELECT * FROM handoff_capsules WHERE {_WAITING} ORDER BY id", [now])]

    def open_passes(self, from_agent: str, from_channel: Optional[str] = None,
                    now: Optional[float] = None) -> List[ParkedCapsule]:
        """The passes `from_agent` parked that still wait for their teammates, oldest
        first; only those passed from `from_channel`, when it's given. None is claimed."""
        now = time.time() if now is None else now
        sql = f"SELECT * FROM handoff_capsules WHERE from_agent = ? AND via = ? AND {_WAITING}"
        params = [from_agent, PASS, now]
        if from_channel is not None:
            sql += " AND from_channel = ?"
            params.append(channel_key(from_channel))
        return [_capsule(record) for record in self.query(f"{sql} ORDER BY id", params)]

    def get(self, capsule_id: int) -> Optional[ParkedCapsule]:
        record = self.select_one("handoff_capsules", {"id": capsule_id})
        return _capsule(record) if record else None
