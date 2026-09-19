"""Turning 1404 registry entries into something an agent can actually read.

The reference instance has 920 live states, 1404 registry entries, 133 devices
and 10 areas. Handing any of those lists to a model verbatim would consume the
context window and bury the few entities the task is about. So this module does
the join and the summarising that a raw API cannot:

  * `summarise()`  -- the whole home as a compact area x domain table. Fixed size
                      regardless of how many entities exist. This is what an
                      agent should read first.
  * `search()`     -- a filtered, flattened view for when the agent knows roughly
                      what it is looking for. Capped, and it says so when it caps.

Both merge the registry (what exists, what it is called, where it lives, whether
it is disabled) with current state (what it is doing). Neither source is
sufficient alone: /api/states omits disabled and hidden entities entirely, and
the registry knows nothing about values.
"""
from typing import Any, Dict, Iterable, List, Optional

# Above this, a result set is summarised rather than listed. Chosen so a full
# response stays in the low hundreds of tokens; the exact number matters less
# than having a ceiling at all.
DEFAULT_LIMIT = 40


def _domain(entity_id: str) -> str:
    return entity_id.split(".", 1)[0] if "." in entity_id else entity_id


def _index_by_id(rows: Iterable[Dict[str, Any]], key: str = "id") -> Dict[Any, Dict[str, Any]]:
    return {row.get(key): row for row in rows or []}


def _area_for(entry: Dict[str, Any], devices_by_id: Dict[Any, Dict[str, Any]]) -> Optional[str]:
    """Resolves an entity's area, following the device when unset.

    HA models this as an override: an entity inherits its device's area unless it
    has an `area_id` of its own. Reading only the entity's field -- the obvious
    implementation -- leaves most entities looking unassigned, because in practice
    the area is set on the device.
    """
    if entry.get("area_id"):
        return entry["area_id"]
    device = devices_by_id.get(entry.get("device_id"))
    return device.get("area_id") if device else None


def build(
    entities: List[Dict[str, Any]],
    devices: List[Dict[str, Any]],
    areas: List[Dict[str, Any]],
    states: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Joins the registries and current state into one flat list of rows.

    The internal representation the other functions work from. One row per
    registry entity, carrying the human-meaningful fields and nothing else.
    """
    devices_by_id = _index_by_id(devices)
    areas_by_id = _index_by_id(areas, key="area_id")
    states_by_id = {s.get("entity_id"): s for s in (states or [])}

    rows = []
    for entry in entities or []:
        entity_id = entry.get("entity_id")
        if not entity_id:
            continue

        area_id = _area_for(entry, devices_by_id)
        area = areas_by_id.get(area_id) or {}
        state = states_by_id.get(entity_id) or {}

        rows.append({
            "entity_id": entity_id,
            "domain": _domain(entity_id),
            "name": (
                entry.get("name")
                or entry.get("original_name")
                or state.get("attributes", {}).get("friendly_name")
                or entity_id
            ),
            "area": area.get("name"),
            "state": state.get("state"),
            # `disabled_by`/`hidden_by` carry *who* disabled it, so presence is the
            # signal, not truthiness of a boolean.
            "disabled": entry.get("disabled_by") is not None,
            "hidden": entry.get("hidden_by") is not None,
        })
    return rows


def summarise(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The whole home, at fixed size.

    Returns totals plus an area x domain breakdown. Deliberately contains no
    entity ids: this is the orientation pass, and an agent that needs specific
    entities should follow up with `search`.
    """
    active = [r for r in rows if not r["disabled"]]

    by_area: Dict[str, Dict[str, int]] = {}
    for row in active:
        area = row["area"] or "(unassigned)"
        by_area.setdefault(area, {})
        by_area[area][row["domain"]] = by_area[area].get(row["domain"], 0) + 1

    by_domain: Dict[str, int] = {}
    for row in active:
        by_domain[row["domain"]] = by_domain.get(row["domain"], 0) + 1

    return {
        "totals": {
            "entities": len(rows),
            "active": len(active),
            "disabled": sum(1 for r in rows if r["disabled"]),
            "hidden": sum(1 for r in rows if r["hidden"]),
            "areas": len({r["area"] for r in active if r["area"]}),
        },
        "by_area": {
            area: dict(sorted(domains.items(), key=lambda kv: -kv[1]))
            for area, domains in sorted(by_area.items(), key=lambda kv: -sum(kv[1].values()))
        },
        "by_domain": dict(sorted(by_domain.items(), key=lambda kv: -kv[1])),
    }


def search(
    rows: List[Dict[str, Any]],
    query: Optional[str] = None,
    domain: Optional[str] = None,
    area: Optional[str] = None,
    include_disabled: bool = False,
    limit: int = DEFAULT_LIMIT,
) -> Dict[str, Any]:
    """Filtered entity lookup, capped and honest about the cap.

    Returns both the rows and the pre-cap total, so an agent that gets 40 of 300
    can tell it is looking at a slice and narrow the filter rather than assuming
    it has seen everything.
    """
    needle = (query or "").strip().lower()
    matches = []

    for row in rows:
        if not include_disabled and row["disabled"]:
            continue
        if domain and row["domain"] != domain:
            continue
        if area and (row["area"] or "").lower() != area.lower():
            continue
        if needle and needle not in row["entity_id"].lower() and needle not in (row["name"] or "").lower():
            continue
        matches.append(row)

    matches.sort(key=lambda r: r["entity_id"])
    return {
        "total": len(matches),
        "returned": min(len(matches), limit),
        "truncated": len(matches) > limit,
        "entities": matches[:limit],
    }
