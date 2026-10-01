"""Which instance -- prod (NAS) or dev (laptop) -- answers for each agent.

Both instances log in with the same bot token per agent, so Discord delivers
every event to both. Without coordination both would reply. Ownership is
coordinated through a Discord thread (the *control thread*): both instances
already hold a gateway connection, so a message posted there by one reaches
the other immediately, with no network path between the machines.

The thread is an append-only log; state is the result of replaying it:

    🔒 claim <agent> @<host>     bot-posted: <host> (a dev instance) holds <agent>
    🔓 release <agent> @<host>   bot-posted: <host> gave <agent> back (clean exit)
    [claim <agent>]              user command: dev takes <agent>; if no dev is
                                 running, prod still goes quiet for it
    [release]                    user command: every agent back to prod
    [status]                     user command: each instance reports its view

Releasing is a *message*, never a deletion, so replaying history at startup
always yields the same state as having watched it live.

Rules:
- prod owns every agent nobody holds;
- dev owns only agents held by its own host, and ignores messages sent before
  its claim (already handled by prod) or older than `DEV_MAX_MESSAGE_AGE`
  (replayed by Discord after the laptop wakes from sleep).
"""
import asyncio
import datetime
import re
import socket
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from core.util.config import Config

PROD = "prod"
DEV = "dev"
MANUAL = "manual"  # held by a user's [claim] with no dev instance answering yet

HISTORY_LIMIT = 200
DEV_MAX_MESSAGE_AGE = datetime.timedelta(minutes=2)
READY_TIMEOUT_SECONDS = 30

_CLAIM_RE = re.compile(r"^🔒 claim `?([\w\-]+)`? @(\S+)")
_RELEASE_RE = re.compile(r"^🔓 release `?([\w\-]+)`? @(\S+)")
_CMD_CLAIM_RE = re.compile(r"^\[\s*claim\s+([\w\-]+)\s*\]$", re.IGNORECASE)
_CMD_RELEASE_RE = re.compile(r"^\[\s*release\s*\]$", re.IGNORECASE)
_CMD_STATUS_RE = re.compile(r"^\[\s*status\s*\]$", re.IGNORECASE)


@dataclass(frozen=True)
class Event:
    kind: str  # "claim" | "release" | "cmd_claim" | "cmd_release" | "cmd_status"
    agent: Optional[str] = None
    host: Optional[str] = None


@dataclass
class Hold:
    holder: str  # dev host name, or MANUAL
    since: Optional[datetime.datetime] = None  # Discord timestamp of the claim


def parse(content: str) -> Optional[Event]:
    text = (content or "").strip()
    if m := _CLAIM_RE.match(text):
        return Event("claim", m.group(1), m.group(2))
    if m := _RELEASE_RE.match(text):
        return Event("release", m.group(1), m.group(2))
    if m := _CMD_CLAIM_RE.match(text):
        return Event("cmd_claim", m.group(1))
    if _CMD_RELEASE_RE.match(text):
        return Event("cmd_release")
    if _CMD_STATUS_RE.match(text):
        return Event("cmd_status")
    return None


def claim_text(agent: str, host: str) -> str:
    return f"🔒 claim {agent} @{host}"


def release_text(agent: str, host: str) -> str:
    return f"🔓 release {agent} @{host}"


def _default_host() -> str:
    return socket.gethostname().split(".")[0] or "dev"


class Ownership:
    """Process-wide view of who holds which agent. One instance per process."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self.role = PROD
        self.host = _default_host()
        self.initial_agents: List[str] = []
        self.holds: Dict[str, Hold] = {}
        self._seen: set = set()
        self._ready = asyncio.Event() if _has_loop() else None
        self._rebuild_lock: Optional[asyncio.Lock] = None
        self._rebuilding = False
        self._pending: list = []
        self._last_rebuild = 0.0
        self._listeners: List[Callable[[str], None]] = []

    @classmethod
    def reset(cls):
        cls._instance = None

    def configure(self, role: str = PROD, agents: Optional[List[str]] = None, host: Optional[str] = None):
        self.role = role
        self.initial_agents = list(agents or [])
        if host:
            self.host = host

    @property
    def control_thread_id(self) -> str:
        return Config().control_thread_id

    def label(self) -> str:
        return f"{self.role}@{self.host}"

    def is_control_channel(self, channel) -> bool:
        return str(getattr(channel, "id", "")) == self.control_thread_id

    # ------------------------------------------------------------------
    # Ownership queries
    # ------------------------------------------------------------------
    def holder(self, agent_id: str) -> Optional[str]:
        hold = self.holds.get(agent_id)
        return hold.holder if hold else None

    def is_mine(self, agent_id: str, message=None) -> bool:
        if not self.is_ready():
            # Prod must not answer for an agent dev may hold before the thread
            # has been read; dev holds nothing until then.
            return False
        hold = self.holds.get(agent_id)
        if self.role == PROD:
            return hold is None
        if hold is None or hold.holder != self.host:
            return False
        created = getattr(message, "created_at", None) if message is not None else None
        if isinstance(created, datetime.datetime):
            if hold.since and created < hold.since:
                return False
            if _utcnow() - created > DEV_MAX_MESSAGE_AGE:
                return False
        return True

    def describe(self, agent_ids: List[str]) -> str:
        lines = [f"**[{self.label()}]**"]
        for agent_id in sorted(agent_ids):
            holder = self.holder(agent_id)
            if holder is None:
                who = "prod"
            elif holder == MANUAL:
                who = "claimed (no dev answering)"
            else:
                who = f"dev@{holder}"
            mine = " ← this instance" if self.is_mine(agent_id) else ""
            lines.append(f"- `{agent_id}`: {who}{mine}")
        unknown = sorted(set(self.holds) - set(agent_ids))
        for agent_id in unknown:
            lines.append(f"- `{agent_id}` (no bot here): {self.holder(agent_id)}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # State changes
    # ------------------------------------------------------------------
    def add_listener(self, fn: Callable[[str], None]):
        self._listeners.append(fn)

    def apply(self, message) -> Optional[Event]:
        """Applies one control-thread message. Idempotent per message id, so
        every bot in this process may call it for the same message."""
        event = parse(getattr(message, "content", ""))
        if event is None:
            return None
        msg_id = getattr(message, "id", None)
        if msg_id is not None:
            if msg_id in self._seen:
                return event
            self._seen.add(msg_id)
        if self._rebuilding:
            self._pending.append(message)
            return event
        changed = self._reduce(self.holds, event, getattr(message, "created_at", None))
        for agent_id in changed:
            self._log_change(agent_id)
            self._notify(agent_id)
        return event

    @staticmethod
    def _reduce(holds: Dict[str, Hold], event: Event, created_at) -> List[str]:
        """Pure state transition. Returns the agents whose holder changed."""
        before = {k: v.holder for k, v in holds.items()}
        if event.kind == "claim":
            holds[event.agent] = Hold(event.host, created_at)
        elif event.kind == "release":
            if event.agent in holds and holds[event.agent].holder == event.host:
                del holds[event.agent]
        elif event.kind == "cmd_claim":
            if event.agent not in holds:
                holds[event.agent] = Hold(MANUAL, created_at)
        elif event.kind == "cmd_release":
            holds.clear()
        after = {k: v.holder for k, v in holds.items()}
        return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))

    def _log_change(self, agent_id: str):
        holder = self.holder(agent_id)
        target = "prod" if holder is None else ("claimed (no dev)" if holder == MANUAL else f"dev ({holder})")
        mine = "this instance" if self.is_mine(agent_id) else "other instance"
        print(f"[Ownership:{self.label()}] {agent_id} → {target} [{mine}]")

    def _notify(self, agent_id: str):
        for fn in list(self._listeners):
            try:
                fn(agent_id)
            except Exception as e:
                print(f"[Ownership] listener error for {agent_id}: {e}")

    async def rebuild(self, bot, force: bool = False):
        """Replays the control thread's history. Called on every bot's
        on_ready; concurrent and back-to-back calls collapse into one."""
        if self._rebuild_lock is None:
            self._rebuild_lock = asyncio.Lock()
        async with self._rebuild_lock:
            loop = asyncio.get_running_loop()
            if not force and self.is_ready() and loop.time() - self._last_rebuild < 10:
                return
            self._rebuilding = True
            self._pending = []
            messages = []
            try:
                thread = await fetch_control_thread(bot)
                async for m in thread.history(limit=HISTORY_LIMIT, oldest_first=False):
                    messages.append(m)
                messages.reverse()
            except Exception as e:
                print(
                    f"[Ownership:{self.label()}] ⚠️ could not read control thread "
                    f"{self.control_thread_id}: {e}. Assuming no claims."
                )
            finally:
                self._rebuilding = False

            before = {k: v.holder for k, v in self.holds.items()}
            holds: Dict[str, Hold] = {}
            seen = set()
            history_ids = {getattr(m, "id", None) for m in messages}
            late = [p for p in self._pending if getattr(p, "id", None) not in history_ids]
            for m in messages + late:
                event = parse(getattr(m, "content", ""))
                if event is not None:
                    self._reduce(holds, event, getattr(m, "created_at", None))
                    seen.add(getattr(m, "id", None))
            self._pending = []
            self.holds = holds
            self._seen |= seen
            self._last_rebuild = loop.time()
            self._ready_event().set()

            print(f"[Ownership:{self.label()}] state from control thread: "
                  f"{ {k: v.holder for k, v in holds.items()} or 'no claims'}")
            after = {k: v.holder for k, v in holds.items()}
            for agent_id in sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k)):
                self._notify(agent_id)

    async def wait_ready(self, timeout: float = READY_TIMEOUT_SECONDS) -> bool:
        if self.is_ready():
            return True
        try:
            await asyncio.wait_for(self._ready_event().wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def _ready_event(self) -> asyncio.Event:
        if self._ready is None:
            self._ready = asyncio.Event()
        return self._ready

    def mark_ready(self):
        """Treats the current (possibly empty) claims as known. For tests."""
        self._ready_event().set()

    def is_ready(self) -> bool:
        return self._ready is not None and self._ready.is_set()

    # ------------------------------------------------------------------
    # Dev-side actions
    # ------------------------------------------------------------------
    async def claim(self, bot, agent_id: str):
        """Posts a claim for agent_id from this (dev) host, unless already held."""
        if self.role != DEV:
            return
        if self.holder(agent_id) == self.host:
            print(f"[Ownership:{self.label()}] {agent_id} already held by this host; reusing claim.")
            return
        thread = await fetch_control_thread(bot)
        sent = await thread.send(claim_text(agent_id, self.host))
        self.apply(sent)

    async def release_all_mine(self, tokens: Dict[str, str]):
        """On clean exit: posts a release for each agent this host holds.

        Goes through the REST API directly because by the time this runs the
        discord.py clients are already closed."""
        if self.role != DEV:
            return
        mine = [a for a, h in self.holds.items() if h.holder == self.host]
        if not mine:
            return
        import aiohttp

        url = f"https://discord.com/api/v10/channels/{self.control_thread_id}/messages"
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            for agent_id in mine:
                token = tokens.get(agent_id)
                if not token:
                    continue
                try:
                    async with http.post(
                        url,
                        json={"content": release_text(agent_id, self.host)},
                        headers={"Authorization": f"Bot {token}"},
                    ) as resp:
                        if resp.status >= 300:
                            raise RuntimeError(f"HTTP {resp.status}: {await resp.text()}")
                    print(f"[Ownership:{self.label()}] released {agent_id} back to prod.")
                except Exception as e:
                    print(
                        f"[Ownership:{self.label()}] ⚠️ could not release {agent_id}: {e}. "
                        f"Post [release] in the control thread."
                    )


async def fetch_control_thread(bot):
    thread_id = int(Config().control_thread_id)
    thread = bot.get_channel(thread_id)
    if thread is None:
        thread = await bot.fetch_channel(thread_id)
    return thread


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _has_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False
