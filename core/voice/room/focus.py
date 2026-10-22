"""The voice focus: which conversation the one voice room is in.

There is one voice channel for the whole system. Asking for another agent --
"switch me to Daisy" -- changes the focus: the text channel the next voice
turn belongs to. Nobody moves between rooms.

Concierge hosts every channel, so a turn under a focus runs exactly as an
untagged message typed in its channel would (`BotRunner.on_message`): through
Concierge, on a `source="discord"` session for that channel. graphs/main then
hands it to the channel's agent, on the same session a typed message gets, so
voice and text share each agent's history and the voice channel holds no
conversation of its own. The lobby -- talking to Concierge itself -- is
#general, Concierge's own channel.

Everything here is pure: built from agent configs, so a test can pin the
table. Turning names into spoken aliases is room_switch.py's job; this module
only says which names lead where, and how each focus is spoken about.
"""
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# The lobby: Concierge's own channel, unless voice_config sets `lobby_channel`.
DEFAULT_LOBBY_CHANNEL = "general"
# What you say to get back to the lobby, besides the channel's own name.
LOBBY_ALIAS = "concierge"


@dataclass(frozen=True)
class VoiceFocus:
    """Where the next voice turn goes."""
    key: str             # the text channel's name
    channel_name: str    # the text channel turns are posted in and keyed on
    agent_id: str        # who answers there: the channel's agent, else Concierge
    label: str           # spoken: "day planning"
    is_lobby: bool = False


def spoken_label(channel_name: str) -> str:
    """'day-planning' -> 'day planning'."""
    return " ".join(part for part in re.split(r"[-_\s]+", (channel_name or "").strip().lower()) if part)


def _names(value: Any) -> List[str]:
    """A config list as clean lowercase names; anything else as no names."""
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).lstrip("#").strip().lower() for item in value if isinstance(item, str) and item.strip()]


def _list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def answering_agent(host_id: str, channel: str, loader: Any) -> str:
    """Who answers an untagged message in `channel` hosted by `host_id`.

    graphs/main's own decision, so voice can't drift from typed routing: the
    sole eligible agent, else the host itself (its concierge).
    """
    from graphs.main.router import decide

    decision = decide(
        prompt="",
        agent_id=host_id,
        channel_name=channel,
        agent_config=loader.get_agent_config(host_id) or {},
        loader=loader,
    )
    return decision.agent_id if decision.is_direct and decision.agent_id else host_id


class FocusDirectory:
    """Every focus the voice room can switch to, built from agent configs."""

    def __init__(self, targets: Dict[str, VoiceFocus], lobby: VoiceFocus, homes: Dict[str, str],
                 names: Dict[str, str], descriptions: Dict[str, str], voice_host: str):
        self.targets = targets             # channel name -> its focus
        self.lobby = lobby
        self.homes = homes                 # agent id -> its home channel
        self.names = names                 # agent id -> display name ("Daisy")
        self.descriptions = descriptions   # agent id -> one-line description
        self.voice_host = voice_host       # Concierge: hosts every channel

    @classmethod
    def build(cls, loader: Any, voice_host: str, lobby_channel: str = DEFAULT_LOBBY_CHANNEL) -> "FocusDirectory":
        """One focus per channel the voice host (Concierge) hosts."""
        lobby_channel = (lobby_channel or DEFAULT_LOBBY_CHANNEL).lstrip("#").lower()
        agent_ids = [a for a in _list(loader.list_agent_ids()) if isinstance(a, str)]
        configs = {a: (loader.get_agent_config(a) or {}) for a in agent_ids}
        configs = {a: c for a, c in configs.items() if isinstance(c, dict)}

        targets: Dict[str, VoiceFocus] = {}
        for channel in _names((configs.get(voice_host) or {}).get("channel_hosts")):
            targets[channel] = VoiceFocus(
                key=channel,
                channel_name=channel,
                agent_id=answering_agent(voice_host, channel, loader),
                label=spoken_label(channel),
                is_lobby=channel == lobby_channel,
            )
        # The lobby exists even when the configs can't be read (a test's loader).
        lobby = targets.get(lobby_channel) or VoiceFocus(
            key=lobby_channel, channel_name=lobby_channel, agent_id=voice_host,
            label=spoken_label(lobby_channel), is_lobby=True,
        )

        # An agent's name leads to its home channel: the first of its channels
        # where it is the one that answers.
        homes: Dict[str, str] = {}
        for agent_id, config in configs.items():
            channels = _names(config.get("channels"))
            if agent_id == voice_host or "*" in channels:
                continue
            for channel in channels:
                target = targets.get(channel)
                if target is not None and target.agent_id == agent_id:
                    homes[agent_id] = channel
                    break

        def text(config, key):
            value = config.get(key)
            return value.strip() if isinstance(value, str) and value.strip() else None

        names = {a: text(c, "name") for a, c in configs.items() if text(c, "name")}
        descriptions = {a: text(c, "description") for a, c in configs.items() if text(c, "description")}
        return cls(targets, lobby, homes, names, descriptions, voice_host)

    # --- lookups ------------------------------------------------------------

    def get(self, key: Optional[str]) -> Optional[VoiceFocus]:
        if key == self.lobby.key:
            return self.lobby
        return self.targets.get(key) if key else None

    def for_channel(self, channel: Any) -> Optional[VoiceFocus]:
        """The focus for a text channel (object or name); a thread counts as its parent."""
        from core.runtime.execution_context import _is_thread

        name = getattr(channel, "name", channel)
        if _is_thread(channel):
            name = getattr(getattr(channel, "parent", None), "name", None) or name
        if not isinstance(name, str):
            return None
        return self.get(name.lstrip("#").lower())

    def alias_sources(self) -> List[Tuple[str, List[str]]]:
        """(focus key, names that mean it), unnormalized: each channel's own
        name; each agent's name, for its home channel; and "concierge", for
        the lobby."""
        sources: List[Tuple[str, List[str]]] = [(key, [t.label]) for key, t in self.targets.items()]
        for agent_id, channel in self.homes.items():
            if self.names.get(agent_id):
                sources.append((channel, [self.names[agent_id]]))
        sources.append((self.lobby.key, [self.lobby.label, LOBBY_ALIAS]))
        return sources

    # --- how a focus is spoken about -----------------------------------------

    def _answered_by_concierge(self, focus: VoiceFocus) -> bool:
        return focus.agent_id == self.voice_host

    def who(self, focus: VoiceFocus) -> str:
        """The name of whoever answers there: 'Daisy', or 'the concierge'."""
        if self._answered_by_concierge(focus):
            return "the concierge"
        return self.names.get(focus.agent_id) or focus.agent_id

    def role(self, focus: VoiceFocus) -> str:
        """How "Do you mean …?" names a focus: 'fitness coach Frankie' -- the
        agent's id read aloud, then its name -- or the channel."""
        if focus.is_lobby:
            return "the concierge"
        if self._answered_by_concierge(focus):
            return focus.label
        name = self.names.get(focus.agent_id) or ""
        role = spoken_label(focus.agent_id)
        return name if not role or role == name.lower() else f"{role} {name}".strip()

    def greeting(self, focus: VoiceFocus) -> str:
        """What you hear on arriving: whoever answers there, introducing itself."""
        concierge = self._answered_by_concierge(focus)
        if concierge and not focus.is_lobby:
            return f"You're in {focus.label}."
        name = self.names.get(focus.agent_id) or ("Concierge" if concierge else focus.agent_id)
        return f"{name} here."

    def whereabouts(self, focus: VoiceFocus) -> str:
        """The answer to "where am I?"."""
        return f"You're in {focus.label}, with {self.who(focus)}."

    def already(self, focus: VoiceFocus) -> str:
        if self._answered_by_concierge(focus) and not focus.is_lobby:
            return f"You're already in {focus.label}."
        return f"You're already with {self.who(focus)}."

    def spoken_alias(self, focus: VoiceFocus) -> str:
        """The name to say for `focus`: its agent's name at the agent's home
        channel, "concierge" for the lobby, else the channel's own name.
        Always one that `build_aliases` maps back to `focus`."""
        from core.voice.room.room_switch import _key, build_aliases

        aliases = build_aliases(self.alias_sources())
        name = self.names.get(focus.agent_id)
        candidates = [LOBBY_ALIAS] if focus.is_lobby else []
        if name and not self._answered_by_concierge(focus) and self.homes.get(focus.agent_id) == focus.key:
            candidates.append(name)
        candidates.append(focus.label)
        for candidate in candidates:
            if aliases.get(_key(candidate)) == focus.key:
                return candidate
        return focus.label

    def words_to_switch(self, focus: VoiceFocus) -> str:
        """What to say to get to `focus`: \"switch to Frankie\"."""
        return f"switch to {self.spoken_alias(focus)}"
