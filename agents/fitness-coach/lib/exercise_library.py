"""The exercise library: library/<group>.json, a JSON array per muscle group.

Each exercise: id, name, primary_muscle, secondary_muscles, equipment, role
(main | accessory | finisher), tracking (weight_reps | reps | time), rep_range
("5-8", "8-12/leg", "30-60s", "2-5 min"), level, cue, media.
"""
import difflib
import json
import os
import re
from typing import Dict, List, NamedTuple, Optional

from core.tooling import Report

# Exact keys of muscle_recovery.json; lowercased, the library file names.
GROUPS = ("Chest", "Back", "Shoulders", "Biceps", "Triceps", "Quads", "Hamstrings", "Glutes", "Calves", "Core")
LOWER_BODY = frozenset({"Quads", "Hamstrings", "Glutes", "Calves"})
ROLES = ("main", "accessory", "finisher")
TRACKINGS = ("weight_reps", "reps", "time")
_DEADLIFT = re.compile(r"deadlift|(^|_)rdl($|_)")


def load_library(library_dir: str, report: Report) -> Dict[str, dict]:
    """id -> exercise, in library order (`_order`). What can't be read goes in `report`.

    A group file that is missing or isn't a JSON array leaves that group without
    exercises. An entry without an id comes back raw; an id listed again keeps
    its first entry; a role or group the tools don't know keeps the entry out of
    the candidates, and an unknown tracking counts as reps.
    """
    library: Dict[str, dict] = {}
    for group in GROUPS:
        path = os.path.join(library_dir, f"{group.lower()}.json")
        try:
            with open(path, encoding="utf-8") as f:
                entries = json.load(f)
        except FileNotFoundError:
            report.problem(path, f"not found: no {group} exercises")
            continue
        except ValueError as e:
            report.problem(path, f"not valid JSON ({e}): no {group} exercises")
            continue
        if not isinstance(entries, list):
            report.problem(path, f"not a JSON array: no {group} exercises")
            continue
        raw, count = [], 0
        for entry in entries:
            exercise_id = entry.get("id") if isinstance(entry, dict) else None
            if not isinstance(exercise_id, str) or not exercise_id.strip():
                raw.append(json.dumps(entry, ensure_ascii=False))
                continue
            if exercise_id in library:
                report.problem(path, f"'{exercise_id}' is listed again; the first entry counts")
                continue
            for problem in entry_problems(entry):
                report.problem(path, f"{exercise_id}: {problem}")
            library[exercise_id] = {**entry, "_order": len(library)}
            count += 1
        report.unparsed(path, raw)
        report.read(path, records=count, skipped=len(raw))
    return library


def entry_problems(entry: dict) -> List[str]:
    """What keeps a library entry from working as written; [] when nothing does."""
    problems = []
    if entry.get("role") not in ROLES:
        problems.append(f"role '{entry.get('role')}' isn't {', '.join(ROLES)}: never a candidate")
    if entry.get("primary_muscle") not in GROUPS:
        problems.append(f"primary_muscle '{entry.get('primary_muscle')}' isn't a muscle group: never a candidate")
    if entry.get("tracking") not in TRACKINGS:
        problems.append(f"tracking '{entry.get('tracking')}' isn't {', '.join(TRACKINGS)}: counted as reps")
    return problems


def is_lower_body(exercise: dict) -> bool:
    """Lower body for the barbell step: legs, plus every deadlift variant (bb_deadlift is Back)."""
    name = f"{exercise.get('id', '')} {exercise.get('name', '')}".lower()
    return exercise.get("primary_muscle") in LOWER_BODY or bool(_DEADLIFT.search(name.replace(" ", "_")))


class RepRange(NamedTuple):
    low: int
    high: int
    seconds: bool  # a timed range ("30-60s", "2-5 min"), in seconds
    side: str      # "/leg", "/side" or ""


def parse_rep_range(text: str) -> RepRange:
    raw = (text or "").strip()
    side = ""
    match = re.search(r"(/\w+)\s*$", raw)
    if match:
        side, raw = match.group(1), raw[: match.start()].strip()
    minutes = raw.endswith("min")
    seconds = minutes or raw.endswith("s")
    numbers = [int(n) for n in re.findall(r"\d+", raw)] or [0]
    scale = 60 if minutes else 1
    return RepRange(numbers[0] * scale, numbers[-1] * scale, seconds, side)


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def resolve(library: Dict[str, dict], ref: str) -> Optional[dict]:
    """An exercise by id, or by its library name (case and punctuation ignored)."""
    ref = (ref or "").strip()
    if ref in library:
        return library[ref]
    wanted = _key(ref)
    for exercise in library.values():
        if wanted and wanted in (_key(exercise["id"]), _key(exercise.get("name", ""))):
            return exercise
    return None


def suggest(library: Dict[str, dict], ref: str, n: int = 3) -> List[str]:
    """Close library ids for an unknown reference."""
    names = {exercise.get("name", ""): exercise["id"] for exercise in library.values()}
    by_name = [names[m] for m in difflib.get_close_matches(ref, list(names), n=n, cutoff=0.5)]
    by_id = difflib.get_close_matches(ref, list(library), n=n, cutoff=0.5)
    return list(dict.fromkeys(by_name + by_id))[:n]
