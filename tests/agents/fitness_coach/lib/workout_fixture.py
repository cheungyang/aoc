"""Made-up pkm/wallet/exercise files in a temp dir, for the fitness-coach tests.

The formats follow the real files: equipment.md's Loading and Stations
sections, library lines, muscle_recovery.json, progression and history lines.
The equipment is a small invented home gym: one gap in the barbell totals, one
in the lever arms, a short dumbbell rack. Like the real library, every muscle
group has its file; the groups LIBRARY doesn't list get an empty one.
"""
import json
import os
import shutil
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

from core.util.config import Config
from agents.fitness_coach.lib.exercise_library import GROUPS

TZ = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 10, 16, 8, 0, tzinfo=TZ)

EQUIPMENT_MD = """# Home Gym
Working weights are in `progression.jsonl`, not here. Weights in lb.

## Loading
- **Barbell**: 45 lb bar. Plates: 35×2, 10×4, 5×2, 2.5×2, 1.25×2.
  - Loadable totals (2.5 lb steps): 45–102.5, 115–172.5.
  - NOT loadable: 105–112.5. Max 172.5.
- **Landmine**: one sleeve, 0–127.5 in 1.25 lb steps (the plates only, not the bar).
- **Lever Arms**: per arm, both arms loaded the same: 0–28.75, 35–63.75 (1.25 lb steps); log one arm's weight.
- **Dumbbells**: adjustable pair, per hand: 5, 10, 15, 20, 25, 30, 40, 50.
- **TRX, bodyweight**: progress with reps or body angle.

## Setup
- Bench: flat or incline, kept by the rack.

## Stations
| Station    | Equipment                                 | Setup |
|------------|-------------------------------------------|-------|
| Rack       | Rack, Barbell, Bench, Plates, Pull-Up Bar | 1 min |
| Landmine   | Landmine, Barbell, Plates                 | 2 min |
| Lever Arms | Lever Arms, Plates                        | 2 min |
| Suspension | TRX                                       | 1 min |
| Floor      | Dumbbells, Bench, Yoga Mat, Ab Wheel      | none  |
"""


def ex(id, name, group, secondary, equipment, role, tracking, rep_range, level="beginner"):
    return {"id": id, "name": name, "primary_muscle": group, "secondary_muscles": secondary,
            "equipment": equipment, "role": role, "tracking": tracking, "rep_range": rep_range,
            "level": level, "cue": f"{name} cue.", "media": f"pkm/wallet/exercise/assets/{id}.gif"}


LIBRARY = {
    "chest": [
        ex("bb_bench_press", "Barbell Bench Press", "Chest", ["Triceps", "Shoulders"], ["Barbell", "Bench", "Rack"], "main", "weight_reps", "5-8", "intermediate"),
        ex("db_incline_press", "Incline Dumbbell Press", "Chest", ["Shoulders", "Triceps"], ["Dumbbells", "Bench"], "accessory", "weight_reps", "8-12"),
        ex("svend_press", "Svend Press", "Chest", ["Shoulders"], ["Plates"], "accessory", "weight_reps", "10-15"),
    ],
    "back": [
        ex("bb_deadlift", "Barbell Deadlift", "Back", ["Hamstrings", "Glutes", "Core"], ["Barbell"], "main", "weight_reps", "3-6", "intermediate"),
        ex("pull_ups", "Pull-Up", "Back", ["Biceps"], ["Pull-Up Bar"], "main", "reps", "5-10", "intermediate"),
    ],
    "quads": [
        ex("bb_back_squat", "Barbell Back Squat", "Quads", ["Glutes", "Hamstrings", "Core"], ["Barbell", "Rack"], "main", "weight_reps", "5-8", "intermediate"),
        ex("db_goblet_squat", "Goblet Squat", "Quads", ["Glutes", "Core"], ["Dumbbells"], "accessory", "weight_reps", "8-12"),
        ex("db_walking_lunge", "Dumbbell Walking Lunge", "Quads", ["Glutes", "Hamstrings"], ["Dumbbells"], "accessory", "weight_reps", "8-12/leg"),
        ex("landmine_goblet_squat", "Landmine Goblet Squat", "Quads", ["Glutes", "Core"], ["Landmine", "Barbell"], "accessory", "weight_reps", "8-12"),
        ex("lever_squat", "Lever Arm Squat", "Quads", ["Glutes", "Hamstrings"], ["Lever Arms", "Rack"], "accessory", "weight_reps", "8-12"),
        ex("trx_pistol_squat", "TRX-Assisted Pistol Squat", "Quads", ["Glutes", "Core"], ["TRX"], "accessory", "reps", "5-8/leg", "intermediate"),
        ex("bb_zercher_squat", "Zercher Squat", "Quads", ["Glutes", "Core", "Back"], ["Barbell", "Rack"], "accessory", "weight_reps", "6-8", "advanced"),
    ],
    "glutes": [
        ex("bb_hip_thrust", "Barbell Hip Thrust", "Glutes", ["Hamstrings", "Core"], ["Barbell", "Bench"], "main", "weight_reps", "8-12", "intermediate"),
    ],
    "core": [
        ex("hanging_leg_raise", "Hanging Leg Raise", "Core", ["Quads"], ["Pull-Up Bar"], "finisher", "reps", "8-12", "intermediate"),
        ex("ab_wheel_rollout", "Ab Wheel Rollout", "Core", ["Back", "Shoulders"], ["Ab Wheel", "Yoga Mat"], "finisher", "reps", "8-12", "intermediate"),
        ex("plank", "Plank", "Core", ["Shoulders"], ["Yoga Mat"], "finisher", "time", "30-60s"),
        ex("mountain_climber", "Mountain Climber", "Core", ["Shoulders", "Quads"], ["Yoga Mat"], "finisher", "time", "1-3 min"),
    ],
}

RECOVERY = {"updated": "2026-10-15T08:00:00-07:00", "Chest": 100, "Back": 60, "Shoulders": 100, "Biceps": 100,
            "Triceps": 100, "Core": 100, "Quads": 100, "Hamstrings": 100, "Calves": 100, "Glutes": 100}


class ExerciseDir:
    """Context manager: Config().pkm_dir points at a temp copy for the duration."""

    def __init__(self, recovery=RECOVERY, progression=(), history=(), library=LIBRARY, equipment=EQUIPMENT_MD):
        self.recovery, self.progression, self.history = recovery, progression, history
        self.library, self.equipment = library, equipment

    def __enter__(self):
        self.root = tempfile.mkdtemp()
        self.dir = os.path.join(self.root, "wallet", "exercise")
        os.makedirs(os.path.join(self.dir, "library"))
        self.write("equipment.md", self.equipment)
        for group in (g.lower() for g in GROUPS):
            entries = self.library.get(group, [])
            body = "[\n" + ",\n".join(json.dumps(e) for e in entries) + "\n]\n" if entries else "[]\n"
            self.write(f"library/{group}.json", body)
        if self.recovery is not None:
            self.write("muscle_recovery.json", json.dumps(self.recovery, indent=2) + "\n")
        self.write("progression.jsonl", "".join(f"{json.dumps(p, separators=(',', ':'))}\n" for p in self.progression))
        self.write("workout_history.jsonl", "".join(f"{json.dumps(h, separators=(',', ':'))}\n" for h in self.history))
        Config().pkm_dir = self.root
        return self

    def __exit__(self, *exc):
        Config().pkm_dir = None
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, name, text):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
            f.write(text)

    def write_rules(self, text):
        """fitness-coach's RULES.md in the temp PKM."""
        folder = os.path.join(self.root, "agents", "fitness-coach")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "RULES.md"), "w", encoding="utf-8") as f:
            f.write(text)

    def read(self, name):
        with open(os.path.join(self.dir, name), encoding="utf-8") as f:
            return f.read()

    def lines(self, name):
        return [json.loads(line) for line in self.read(name).splitlines() if line.strip()]
