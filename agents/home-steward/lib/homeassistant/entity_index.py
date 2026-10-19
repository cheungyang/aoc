"""The Home Assistant entities an agent may control: home-steward's device list.

Device control cost four model calls: the model knew the user's words ("the
kitchen lights") but not the entity ids, so it looked them up before acting.
With the ids at hand it acts without a lookup, and status questions about the
same devices ("what's the thermostat set to?") go straight to `get_state`.
The `device_list` tool keeps them in the user's PKM (pkm/wallet/home/DEVICES.md),
so no device name reaches this repository: it fetches them on its first call
and on the weekly refresh, and reads the file back otherwise.

What goes in, and why it stays small. The reference home has 1,104 registry
entries and 651 live states; 43 entities make the list:

  * Only the domains a spoken request actuates: light, switch, climate, fan and
    media_player. Covers are on the guard deny-list (`guards.BLOCKED_DOMAINS`),
    so listing them would only invite a refused call.
  * Only entities a person would name: none with an `entity_category` (device
    settings such as a plug's LED or child lock), none hidden (switch_as_x hides
    the switch it wraps), and none `unavailable` -- on the reference home those
    are mostly dead duplicates left by a cloud integration.
  * States are left out: they change by the minute, and a list refreshed
    weekly would show stale ones.
  * `render` gives the model one line per area, ids only. A friendly name
    follows an id only when the id doesn't already say it. At most
    `MAX_ENTRIES`, a guard against a much larger home; the rest are counted,
    and `search_registry` finds them.

The file is plain markdown, so it reads well in Obsidian: `render_file` writes a
heading per area and a line per device, name included, and `parse_file` reads
it back.
"""
import itertools
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from agents.home_steward.lib.homeassistant import guards
from agents.home_steward.lib.homeassistant.ws import HomeAssistantWebSocket

INDEX_DOMAINS = ("light", "switch", "climate", "fan", "media_player")

MAX_ENTRIES = 200

# A refresh waits at most this long for Home Assistant at each step (connecting, each reply).
FETCH_TIMEOUT_SECONDS = 5

NO_AREA = "No area"

# The file's first line: edits are overwritten, so the file says so where the user reads it.
NOTICE = "Written by home-steward's device_list and refreshed weekly, so edits are overwritten."
REFRESHED = "Refreshed:"
STAMP_FORMAT = "%Y-%m-%d %H:%M"

_HEADING = re.compile(r"^## (?P<area>.+)$")
# HA entity ids are lowercase letters, digits and underscores, so the id ends at the colon.
_DEVICE = re.compile(r"^- (?P<entity_id>[a-z0-9_]+\.[a-z0-9_]+)(?::\s*(?P<name>.*))?$")


@dataclass(frozen=True)
class Entry:
    area: str        # "" when the entity has no area
    entity_id: str
    name: str        # friendly name


@dataclass
class DeviceFile:
    """DEVICES.md, read back by `parse_file`."""
    refreshed: Optional[str]  # "2026-10-18 20:00"; None when the file has no Refreshed line
    entries: List[Entry] = field(default_factory=list)
    unrecognized: List[str] = field(default_factory=list)  # lines no rule reads, as written


def build(
    entities: Iterable[Dict[str, Any]],
    devices: Iterable[Dict[str, Any]],
    areas: Iterable[Dict[str, Any]],
    states: Iterable[Dict[str, Any]],
) -> List[Entry]:
    """The indexable entities, sorted by area (no area last), then entity id.

    Starts from live states: an entity with no state is disabled or not loaded,
    and can't be controlled. The area follows HA's rule (the entity's own, else
    its device's), and the name is the state's friendly name, which is the name
    the user sees in Home Assistant.
    """
    registry = {e.get("entity_id"): e for e in entities or [] if e.get("entity_id")}
    device_area = {d.get("id"): d.get("area_id") for d in devices or []}
    area_names = {a.get("area_id"): a.get("name") for a in areas or []}

    entries = []
    for state in states or []:
        entity_id = str(state.get("entity_id") or "")
        if guards.domain_of(entity_id) not in INDEX_DOMAINS or state.get("state") == "unavailable":
            continue
        entry = registry.get(entity_id) or {}
        if entry.get("entity_category") or entry.get("hidden_by") or entry.get("disabled_by"):
            continue
        area_id = entry.get("area_id") or device_area.get(entry.get("device_id"))
        name = (
            (state.get("attributes") or {}).get("friendly_name")
            or entry.get("name")
            or entry.get("original_name")
            or entity_id
        )
        entries.append(Entry(area_names.get(area_id) or "", entity_id, str(name)))
    return sorted(entries, key=lambda e: (not e.area, e.area.lower(), e.entity_id))


def _words(text: str) -> set:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _item(entry: Entry) -> str:
    """`light.kitchen_ceiling`, or `switch.patio_plug "Patio string lights"`.

    HA derives an entity id from the name it was created with, so the id usually
    says the name already: the name is shown only when one of its words is
    missing from the id.
    """
    if _words(entry.name) <= _words(entry.entity_id.split(".", 1)[-1]):
        return entry.entity_id
    return f'{entry.entity_id} "{entry.name}"'


def render(entries: Iterable[Entry]) -> str:
    """One line per area, `Kitchen: light.kitchen_ceiling, ...`, in `build` order.

    Lists at most `MAX_ENTRIES` and says how many it left out.
    """
    entries = list(entries)
    shown = entries[:MAX_ENTRIES]
    lines = [
        f"{area or NO_AREA}: " + ", ".join(_item(e) for e in group)
        for area, group in itertools.groupby(shown, key=lambda e: e.area)
    ]
    if len(entries) > len(shown):
        lines.append(f"(+{len(entries) - len(shown)} more not listed)")
    return "\n".join(lines)


def fetch(timeout: int = FETCH_TIMEOUT_SECONDS) -> List[Entry]:
    """Reads the registries and states over one WebSocket and builds the index."""
    with HomeAssistantWebSocket(timeout=timeout) as ws:
        entities = ws.entities()
        devices = ws.devices()
        areas = ws.areas()
        states = ws.command("get_states")
    return build(entities, devices, areas, states)


# -- The file ------------------------------------------------------------------

def render_file(entries: Iterable[Entry], refreshed: datetime) -> str:
    """DEVICES.md's text: the notice, when it was refreshed, then a heading per area and a line per device.

    Every device line carries its name, so the file reads well on its own and
    `parse_file` gets each entry back whole.
    """
    lines = [NOTICE, "", f"{REFRESHED} {refreshed.strftime(STAMP_FORMAT)}"]
    for area, group in itertools.groupby(entries, key=lambda e: e.area):
        lines += ["", f"## {_one_line(area) or NO_AREA}"]
        lines += [f"- {e.entity_id}: {_one_line(e.name)}" for e in group]
    return "\n".join(lines) + "\n"


def parse_file(text: str) -> DeviceFile:
    """DEVICES.md's entries, when it was refreshed, and the lines it doesn't recognize, as written.

    A device line without a name reads back with an empty one, so `render` shows its id alone.
    """
    saved, area = DeviceFile(refreshed=None), ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped == NOTICE:
            continue
        stamp = stripped[len(REFRESHED):].strip() if stripped.startswith(REFRESHED) else None
        heading, device = _HEADING.match(stripped), _DEVICE.match(stripped)
        if stamp is not None and _is_stamp(stamp):
            saved.refreshed = stamp
        elif heading:
            area = heading.group("area").strip()
            area = "" if area == NO_AREA else area
        elif device:
            saved.entries.append(Entry(area, device.group("entity_id"), (device.group("name") or "").strip()))
        else:
            saved.unrecognized.append(line)
    return saved


def _one_line(text: str) -> str:
    """`text` with its whitespace runs (a stray newline included) as single spaces."""
    return " ".join(str(text).split())


def _is_stamp(text: str) -> bool:
    try:
        datetime.strptime(text, STAMP_FORMAT)
    except ValueError:
        return False
    return True
