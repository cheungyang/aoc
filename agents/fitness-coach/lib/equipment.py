"""Loadable weights and stations, parsed from equipment.md.

equipment.md is the user's own description of the home gym: its "Loading"
section lists what each implement can be loaded to, its "Stations" table which
equipment sits together. Parsing it, rather than copying its numbers here, keeps
the tools right when a plate or a dumbbell dial changes. All weights are lb.
"""
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from core.tooling import Report

# Implements whose load comes in fixed steps. When an exercise lists several,
# the first one here decides: a landmine row uses the Barbell, but its load is
# the plate weight on the Landmine sleeve; calf raises stand on a plate, but
# their load is the bar or the dumbbells.
LOAD_IMPLEMENTS = ("Landmine", "Lever Arms", "Barbell", "Dumbbells", "Plates")

_EPS = 1e-6
_NUM = r"\d+(?:\.\d+)?"
_RANGE = re.compile(rf"({_NUM})\s*[–-]\s*({_NUM})")
_STEP = re.compile(rf"({_NUM})\s*lb steps")
_PLATE = re.compile(rf"({_NUM})\s*[×x]\s*(\d+)")
_ITEM = re.compile(r"^-\s+\*\*(.+?)\*\*:?\s*(.*)$")


@dataclass(frozen=True)
class Equipment:
    loads: Dict[str, Tuple[float, ...]]   # implement -> sorted loadable weights
    stations: Dict[str, Tuple[str, ...]]  # station -> its equipment, in table order


def parse_equipment(text: str, source: str, report: Report) -> Equipment:
    """The loads and stations in equipment.md's text. What can't be read goes in `report`.

    Loading: `- **Name**: text` bullets, each with its indented sub-bullets.
    Stations: the table's rows, after its header. Other lines are notes. A
    bullet or row that doesn't parse comes back raw, and so does a load
    implement whose weights can't be read.
    """
    loading, station_lines = _section(text, "Loading"), _section(text, "Stations")
    if loading is None:
        report.problem(source, "no '## Loading' section: loads are not snapped to buildable weights")
    if station_lines is None:
        report.problem(source, "no '## Stations' section: every station shows as 'Any'")
    items, unknown = _items(loading or [])
    loads: Dict[str, Tuple[float, ...]] = {}
    plates: List[Tuple[float, int]] = []
    for name, parts, lines in items:
        weights = _item_loads(parts)
        if weights:
            loads[name] = weights
        elif name in LOAD_IMPLEMENTS:
            unknown.extend(lines)
        for part in parts:
            at = part.lower().find("plates:")
            found = [(float(w), int(n)) for w, n in _PLATE.findall(part[at + 7:])] if at != -1 else []
            plates = found or plates
    if plates and "Plates" not in loads:
        loads["Plates"] = _plate_sums(plates)
    stations, bad_rows = _stations(station_lines or [])
    if loading is not None and not loads:
        report.problem(source, "'## Loading' lists no loadable weights: loads are not snapped to buildable weights")
    if station_lines is not None and not stations:
        report.problem(source, "'## Stations' has no table rows: every station shows as 'Any'")
    report.unparsed(source, unknown + bad_rows)
    return Equipment(loads=loads, stations=stations)


def _section(text: str, title: str) -> Optional[List[str]]:
    """The lines under `## <title>`, up to the next `## ` heading; None without that heading."""
    lines, inside, found = [], False, False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line[3:].strip().lower() == title.lower()
            found = found or inside
        elif inside:
            lines.append(line)
    return lines if found else None


def _items(lines: Iterable[str]) -> Tuple[List[Tuple[str, List[str], List[str]]], List[str]]:
    """Top-level bullets `- **Name**: text`, each with its indented sub-bullets and raw lines,
    plus the bullets that aren't in that form. Lines that aren't bullets are notes."""
    items: List[Tuple[str, List[str], List[str]]] = []
    unknown: List[str] = []
    for line in lines:
        if not line.strip():
            continue
        match = _ITEM.match(line)
        if match:
            items.append((match.group(1).strip(), [match.group(2).strip()], [line]))
        elif items and line[:1] in (" ", "\t") and line.strip().startswith("-"):
            items[-1][1].append(line.strip()[1:].strip())
            items[-1][2].append(line)
        elif line.lstrip()[:1] in ("-", "*"):
            unknown.append(line)
    return items, unknown


def _item_loads(parts: List[str]) -> Optional[Tuple[float, ...]]:
    """Ranges with a step ("45–102.5, 115–172.5 (2.5 lb steps)") or a list after a colon."""
    usable = [p for p in parts if "not loadable" not in p.lower()]
    loadable = [p for p in usable if "loadable" in p.lower()]
    joined = " ".join(loadable or usable)
    step, ranges = _STEP.search(joined), _RANGE.findall(joined)
    if step and ranges:
        return _expand(ranges, float(step.group(1)))
    head = parts[0]
    if ":" in head:
        tail = head.rsplit(":", 1)[1]
        numbers = re.findall(_NUM, tail)
        if len(numbers) >= 2 and "×" not in tail:
            return tuple(sorted({float(n) for n in numbers}))
    return None


def _expand(ranges: Sequence[Tuple[str, str]], step: float) -> Tuple[float, ...]:
    values = set()
    for low, high in ranges:
        low, high = float(low), float(high)
        for i in range(int(round((high - low) / step)) + 1):
            values.add(round(low + i * step, 4))
    return tuple(sorted(values))


def _plate_sums(plates: Sequence[Tuple[float, int]]) -> Tuple[float, ...]:
    """Every total the plate pile can make (plates held or stacked), above zero."""
    sums = {0}
    for weight, count in plates:
        cents = int(round(weight * 100))
        for _ in range(count):
            sums |= {s + cents for s in sums}
    return tuple(sorted(s / 100 for s in sums if s > 0))


def _stations(lines: Iterable[str]) -> Tuple[Dict[str, Tuple[str, ...]], List[str]]:
    """station -> its equipment from the table rows after the header, plus rows that don't read."""
    rows = [line for line in lines if line.strip().startswith("|")]
    stations: Dict[str, Tuple[str, ...]] = {}
    bad: List[str] = []
    for row in rows[1:]:  # the first row is the header
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if cells and set(cells[0]) <= set("-: ") and all(set(c) <= set("-: ") for c in cells):
            continue  # the |---|---| line under the header
        if len(cells) < 2 or not cells[0] or not cells[1]:
            bad.append(row)
            continue
        stations[cells[0]] = tuple(e.strip() for e in cells[1].split(",") if e.strip())
    return stations, bad


# --------------------------------------------------------------------- lookups

def implement_for(equipment: Sequence[str]) -> Optional[str]:
    """The implement whose weight an exercise's load counts, or None for bodyweight."""
    return next((name for name in LOAD_IMPLEMENTS if name in equipment), None)


def station_for(equipment: Sequence[str], stations: Dict[str, Tuple[str, ...]]) -> str:
    """The station an exercise is done at: one holding all its equipment, preferring the
    home of its first item (Barbell + Rack -> Rack); otherwise the first item's home
    (Lever Arms + Rack -> Lever Arms, since the arms mount on the rack). "Any" when it
    needs nothing.
    """
    if not equipment:
        return "Any"
    names = list(stations)
    covering = [s for s in names if set(equipment) <= set(stations[s])]
    homes = [s for s in names if equipment[0] in stations[s]]
    for station in homes:
        if station in covering:
            return station
    if covering:
        return covering[0]
    if homes:
        return homes[0]
    for item in equipment:
        for station in names:
            if item in stations[station]:
                return station
    return "Any"


def is_loadable(weights: Sequence[float], x: float) -> bool:
    return any(abs(w - x) < _EPS for w in weights)


def snap_down(weights: Sequence[float], x: float) -> Optional[float]:
    """The heaviest loadable weight at or below x; None below the lightest."""
    below = [w for w in weights if w <= x + _EPS]
    return below[-1] if below else None


def snap_up(weights: Sequence[float], x: float) -> Optional[float]:
    """The lightest loadable weight at or above x; None above the heaviest."""
    above = [w for w in weights if w >= x - _EPS]
    return above[0] if above else None


def step_up(implement: Optional[str], weights: Sequence[float], load: float, lower_body: bool,
            rules: Mapping[str, Any]) -> Optional[float]:
    """The raw "one step up" target, which may fall in a gap; None at the heaviest dial.

    Barbell: + `barbell_step_lower` for lower-body lifts, else + `barbell_step_upper`;
    dumbbells: the next dial; anything else: + `plate_step`.
    """
    if implement == "Dumbbells":
        return next((w for w in weights if w > load + _EPS), None)
    if implement == "Barbell":
        return load + (rules["barbell_step_lower"] if lower_body else rules["barbell_step_upper"])
    return load + rules["plate_step"]


def step_down(implement: Optional[str], weights: Sequence[float], load: float, lower_body: bool,
              rules: Mapping[str, Any]) -> Optional[float]:
    if implement == "Dumbbells":
        lighter = [w for w in weights if w < load - _EPS]
        return lighter[-1] if lighter else None
    if implement == "Barbell":
        return load - (rules["barbell_step_lower"] if lower_body else rules["barbell_step_upper"])
    return load - rules["plate_step"]


def ladder(implement: Optional[str], weights: Sequence[float], load: float, lower_body: bool,
           rules: Mapping[str, Any], down: int = 2, up: int = 2, through: Optional[float] = None) -> List[float]:
    """Loadable weights one 🔼/🔽 step apart around `load`: a gap is jumped, not landed in.

    `down` steps below `load` and `up` above it. With `through`, a heavier load,
    the climb passes it first and goes `up` steps beyond.
    """
    rungs = [load]
    current, left = load, up
    while left > 0:
        target = step_up(implement, weights, current, lower_body, rules)
        nxt = (snap_up(weights, target) or weights[-1]) if target is not None else None
        if nxt is None or nxt <= current + _EPS:
            break
        rungs.append(nxt)
        current = nxt
        if through is None or current > through + _EPS:
            left -= 1
    current = load
    for _ in range(down):
        target = step_down(implement, weights, current, lower_body, rules)
        nxt = (snap_down(weights, target) or weights[0]) if target is not None else None
        if nxt is None or nxt >= current - _EPS:
            break
        rungs.insert(0, nxt)
        current = nxt
    return rungs
