"""One read of everything in pkm/wallet/exercise, parsed, for both workout tools."""
from dataclasses import dataclass
from typing import Dict, List, Optional

from core.tooling import Report
from agents.fitness_coach.lib.equipment import Equipment, parse_equipment
from agents.fitness_coach.lib.exercise_files import (
    EQUIPMENT_FILE, HISTORY_FILE, LIBRARY_DIR, PROGRESSION_FILE, RECOVERY_FILE,
    data_path, read_json, read_lines, read_text,
)
from agents.fitness_coach.lib.exercise_library import GROUPS, load_library
from agents.fitness_coach.lib.muscle_recovery import check_record
from agents.fitness_coach.lib.progression import parse_progression
from agents.fitness_coach.lib.workout_history import parse_history


@dataclass
class WorkoutData:
    library: Dict[str, dict]
    equipment: Equipment
    recovery: Optional[dict]
    progression_lines: List[str]
    progression: Dict[str, dict]
    history: List[dict]


def load_data(report: Report) -> WorkoutData:
    """Reads the five data sources. What can't be read goes in `report`.

    No exercise in the library is required data (failed). A missing
    equipment.md leaves loads unsnapped and stations unknown, and a broken
    muscle_recovery.json counts every group as recovered (partial). A missing
    recovery, progression or history file is a first run: nothing to report.
    """
    library_dir = data_path(LIBRARY_DIR)
    library = load_library(library_dir, report)
    if not library:
        report.missing(library_dir, "no exercises found")

    equipment_path = data_path(EQUIPMENT_FILE)
    equipment_text = read_text(equipment_path)
    if equipment_text is None:
        report.problem(equipment_path, "not found: loads are not snapped to buildable weights, stations show as 'Any'")
        equipment = Equipment(loads={}, stations={})
    else:
        equipment = parse_equipment(equipment_text, equipment_path, report)
        report.read(equipment_path, records=len(equipment.loads) + len(equipment.stations))

    recovery_path = data_path(RECOVERY_FILE)
    try:
        recovery = check_record(read_json(recovery_path), recovery_path, report)
    except ValueError as e:
        report.problem(recovery_path, f"not valid JSON ({e}): every group counts as recovered")
        recovery = None
    if recovery is not None:
        report.read(recovery_path, records=sum(1 for group in GROUPS if group in recovery))

    progression_path = data_path(PROGRESSION_FILE)
    progression_lines = read_lines(progression_path)
    progression = parse_progression(progression_lines, progression_path, report)
    report.read(progression_path, records=len(progression), skipped=len(progression_lines) - len(progression))

    history_path = data_path(HISTORY_FILE)
    history_lines = read_lines(history_path)
    history = parse_history(history_lines, history_path, report)
    report.read(history_path, records=len(history), skipped=len(history_lines) - len(history))
    return WorkoutData(
        library=library,
        equipment=equipment,
        recovery=recovery,
        progression_lines=progression_lines,
        progression=progression,
        history=history,
    )
