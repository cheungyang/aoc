# fitness-coach: how its tools work

Both tools read `pkm/wallet/exercise/` (the library, equipment.md, muscle_recovery.json, progression.jsonl and workout_history.jsonl) and the thresholds in `pkm/agents/fitness-coach/RULES.md`; a key RULES.md doesn't set keeps its default from `lib/rules.py`. Weights are lb. The code computes; the coach decides, in the coach_home_workout skill: which exercises to pick, whether to take a break discount, and the load after a weight gap.

Status: `ok` when every file read fully; `partial` when a line or entry wasn't recognized (it comes back raw in the issues), a library file or equipment.md is missing, muscle_recovery.json can't be read (every group then counts as recovered, and the issue says so), a RULES.md line can't be read (its default applies), sleep couldn't be read, or an id is unknown; `failed` when the library has no exercise at all, or none of the ids is in it.

```python
>>> from datetime import date, datetime
>>> from zoneinfo import ZoneInfo
>>> from core.tooling import Report
>>> from agents.fitness_coach.lib.rules import defaults
>>> rules = defaults()
>>> rules["recovery_per_hour"], rules["deload_after_days"], rules["deload_pct"]
(2.0, 21, 10)
```

## The files

### equipment.md

The `## Loading` section lists each implement as a `- **Name**: text` bullet, with indented sub-bullets: ranges with a step ("45–102.5, 115–172.5 (2.5 lb steps)"), or a list after a colon. A plate list (`Plates: 35×2, 10×4, …`) also gives every total the plates can make. The `## Stations` table's rows, after its header, say which equipment sits together. Other lines are notes. A bullet or row that doesn't parse comes back raw, and so does a load implement whose weights can't be read:

```python
>>> from agents.fitness_coach.lib.equipment import parse_equipment
>>> report = Report()
>>> gym = parse_equipment("\n".join([
...     "## Loading",
...     "- **Barbell**: 45 lb bar. Plates: 35×2, 10×4, 5×2, 2.5×2, 1.25×2.",
...     "  - Loadable totals (2.5 lb steps): 45–102.5, 115–172.5.",
...     "- **Dumbbells**: per hand: 5, 10, 15, 20, 25, 30, 40, 50.",
...     "- Kettlebell 35 lb",
...     "## Stations",
...     "Use one or two stations per workout.",
...     "| Station | Equipment |",
...     "|---|---|",
...     "| Rack | Barbell, Rack, Bench |",
...     "| Floor | Dumbbells, Yoga Mat |",
... ]), "equipment.md", report)
>>> gym.loads["Barbell"][:3], gym.loads["Barbell"][-1], 105.0 in gym.loads["Barbell"]
((45.0, 47.5, 50.0), 172.5, False)
>>> gym.stations["Floor"]
('Dumbbells', 'Yoga Mat')
>>> [(issue.headline(), issue.lines) for issue in report.issues]
[('equipment.md: 1 line not recognized', ['- Kettlebell 35 lb'])]
```

### The library, progression and history

- `library/<group>.json`: a JSON array per muscle group. An entry without an id comes back raw; an id listed again keeps its first entry; a role or primary muscle the tools don't know keeps the entry out of the candidates. A missing group file leaves that group without exercises.
- `progression.jsonl`: one line per exercise done, written only by log_workout_session: `{"id":"bb_back_squat","last":"2026-10-13","load":135,"reps":6,"next_load":145}`. A line that isn't a JSON object with an id comes back raw, and is kept as it is when the file is rewritten; for a repeated id the first line counts.
- `workout_history.jsonl`: one line per exercise per session, only ever appended. A line without an id or a date comes back raw.

```python
>>> from agents.fitness_coach.lib.progression import parse_progression
>>> report = Report()
>>> parse_progression(['{"id":"plank","last":"2026-10-13","secs":45,"next_secs":50}', "plank 50s"], "progression.jsonl", report)
{'plank': {'id': 'plank', 'last': '2026-10-13', 'secs': 45, 'next_secs': 50}}
>>> report.issue_lines()
['progression.jsonl: 1 line not recognized']
```

## workout_setup

### Recovery

A group's recovery now is min(100, stored + `recovery_per_hour` × hours since `updated`), rounded down. A group missing from muscle_recovery.json counts as recovered. At 100 a group is eligible for a main or accessory lift.

Quads were stored at 40 and Back at 95 at 22:00; ten hours later, at the default 2 an hour, Quads are at 60 and Back at 100:

```python
>>> from agents.fitness_coach.lib.muscle_recovery import current_recovery, next_ready
>>> now = datetime(2026, 10, 16, 8, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
>>> values = current_recovery({"updated": "2026-10-15T22:00:00-07:00", "Quads": 40, "Back": 95}, now, rules)
>>> values["Quads"], values["Back"], values["Chest"]
(60, 100, 100)
```

With no group eligible there are no rows; the note names the group that reaches 100 first. Back at 80 needs 20 points, 10 hours:

```python
>>> group, when = next_ready({**{g: 50 for g in values}, "Back": 80}, now, rules)
>>> group, when.strftime("%a %H:%M")
('Back', 'Fri 18:00')
```

### Sleep

`sleep_h` adds up the sleep sessions that ended today, leaving out those Google Health flags as naps. `short_sleep` is true under `short_sleep_hours`. When Google Health can't be read, sleep is unknown (partial), never guessed, and the workout goes ahead.

```python
>>> from agents.fitness_coach.lib.workout_plan import last_night_hours
>>> last_night_hours({"sessions": [{"wake_date": "2026-10-16", "minutes_asleep": 300},
...                                {"wake_date": "2026-10-16", "minutes_asleep": 30, "main_sleep": False}]},
...                  date(2026, 10, 16))
5.0
```

### Candidates

`recent_mains` lists the main lifts done within `main_lookback_days`, each with the date its current run began: walking back from the latest use, a pause longer than `main_run_gap_days` starts a new run. The squat's run began on Sep 10, after a 40-day pause; the bench, last done 57 days ago, isn't recent:

```python
>>> import json
>>> from agents.fitness_coach.lib.workout_history import parse_history, recent_mains
>>> uses = [("bb_back_squat", "2026-08-01"), ("bb_back_squat", "2026-09-10"), ("bb_back_squat", "2026-09-28"),
...         ("bb_back_squat", "2026-10-13"), ("bb_bench_press", "2026-08-20")]
>>> history = parse_history([json.dumps({"date": day, "id": i, "sets": [{"load": 100, "reps": 5, "signal": "completed"}]})
...                          for i, day in uses], "workout_history.jsonl", Report())
>>> recent_mains(history, {"bb_back_squat": {"role": "main"}, "bb_bench_press": {"role": "main"}}, date(2026, 10, 16), rules)
[{'id': 'bb_back_squat', 'since': '2026-09-10'}]
```

The rows cover the eligible groups only:

- main: the recent mains first, then the best of each group in turn, at most `max_mains`;
- accessory: per group, `accessory_budget` split evenly across the groups, at least `accessories_per_group_min` and at most `accessories_per_group_max`;
- finisher: at most `max_finishers`, Core first.

Within a role, an exercise done within the last `rotate_days` ranks last, an advanced one never started ranks after the rest, then library order decides. Accessories and finishers spread over stations: the mains' stations and Floor first. With 4 groups eligible each gets 4 accessories; with all 10, each gets 2:

```python
>>> from agents.fitness_coach.lib.workout_plan import accessories_per_group
>>> accessories_per_group(4, rules), accessories_per_group(10, rules)
(4, 2)
```

### The prescription

Each row's `days` counts the days since the exercise was last done (`-` never), and `rx` is the prescription per set:

- never done: `(new)`, a calibration start at the lightest buildable weight (empty bar, lightest dial), or the library range for bodyweight and timed holds;
- otherwise progression.jsonl's next target, a load snapped down to a buildable weight.

```python
>>> from agents.fitness_coach.lib.progression import prescribe
>>> squat = {"id": "bb_back_squat", "primary_muscle": "Quads", "equipment": ["Barbell", "Rack"],
...          "tracking": "weight_reps", "rep_range": "5-8"}
>>> today = date(2026, 10, 16)
>>> prescribe(squat, None, today, gym, rules).text()
'5-8 @ 45 (new)'
>>> prescribe(squat, {"id": "bb_back_squat", "last": "2026-10-13", "load": 135, "reps": 6, "next_load": 145}, today, gym, rules).text()
'5-8 @ 145'
```

Last done more than `deload_after_days` ago, `rx` also shows the break discount, `deload_pct` % off: the load snapped down to a buildable weight with the reps back to the range, or the reps (rounded down, at least 1) or seconds (rounded down to 5 s, at least 5). Whether to take it is the coach's call. 32 days off: 145 × 0.9 = 130.5, so 130; for the bench, 120 × 0.9 = 108 can't be built, so 102.5:

```python
>>> rx = prescribe(squat, {"id": "bb_back_squat", "last": "2026-09-14", "load": 135, "reps": 6, "next_load": 145}, today, gym, rules)
>>> rx.days, rx.text()
(32, '5-8 @ 145 (-10%: 5-8 @ 130)')
>>> bench = {"id": "bb_bench_press", "primary_muscle": "Chest", "equipment": ["Barbell", "Bench", "Rack"],
...          "tracking": "weight_reps", "rep_range": "5-8"}
>>> prescribe(bench, {"id": "bb_bench_press", "last": "2026-09-20", "load": 115, "reps": 6, "next_load": 120}, today, gym, rules).text()
'5-8 @ 120 (-10%: 5-8 @ 102.5)'
>>> plank = {"id": "plank", "primary_muscle": "Core", "equipment": ["Yoga Mat"], "tracking": "time", "rep_range": "30-60s"}
>>> prescribe(plank, {"id": "plank", "last": "2026-09-01", "secs": 45, "next_secs": 50}, today, gym, rules).text()
'50s (-10%: 45s)'
```

### Let's Go: loads and gaps

One step is: barbell + `barbell_step_upper` for upper-body lifts, + `barbell_step_lower` for lower-body lifts and every deadlift; dumbbells, the next dial; landmine, lever arms and plates, + `plate_step`.

`loads` lists buildable weights one step apart, two steps below the start and two above. A step that lands in a gap goes on to the next buildable weight: 🔼 from 100 is 115. A `(new)` pick has no steps down. With a break discount, the list runs from two steps below the discounted load to two above the target, so either start works:

```python
>>> from agents.fitness_coach.lib.equipment import ladder
>>> from agents.fitness_coach.lib.progression import num
>>> barbell = gym.loads["Barbell"]
>>> [num(w) for w in ladder("Barbell", barbell, 145, True, rules)]
[125, 135, 145, 155, 165]
>>> [num(w) for w in ladder("Barbell", barbell, 100, False, rules)]
[90, 95, 100, 115, 120]
>>> [num(w) for w in ladder("Barbell", barbell, 45, True, rules, down=0)]
[45, 55, 65]
>>> [num(w) for w in ladder("Barbell", barbell, 135, True, rules, through=145)]
[115, 125, 135, 145, 155, 165]
```

`gap` names each start whose next step up can't be built: `from`, the weights on either side of the gap (`below`, `above`) and the top of the rep range (`top_reps`), for the coach's pick at wrap-up. Past the heaviest weight there is nothing to jump to, so that is no gap.

```python
>>> from agents.fitness_coach.lib.progression import gap_after
>>> gap_after(bench, 100, gym, rules).as_dict()
{'from': 100, 'below': 102.5, 'above': 115, 'top_reps': 8}
>>> gap_after(bench, 102.5, gym, rules).as_dict()
{'from': 102.5, 'below': 102.5, 'above': 115, 'top_reps': 8}
>>> print(gap_after(squat, 145, gym, rules))
None
```

## log_workout_session

### What stops the call

Nothing is written when an exercise is unknown or listed twice, a set misses a field its tracking needs, `start` is more than 5 minutes ahead or more than 24 hours ago, `duration_minutes` is under 1, the same exercise is already logged in a session starting within 90 minutes, a gap needs `next_load`, or `next_load` comes with an exercise that isn't a loaded lift. The errors say which. These limits protect the files, so they stay in code. A start plus duration past now is trimmed to now.

### Next targets

- A loaded lift with every set ✅ goes one step up when that weight can be built. When one step up falls in a gap, the call needs the coach's `next_load`; without one, it stops and lists the two choices. At the heaviest weight, or past it, the heaviest weight.
- After any 🔼/🔽, or when cut short, the last set's load and reps (or seconds) are held as the next target.
- A `next_load` from the coach replaces the load either rule gives, snapped down to a buildable weight (the lightest one if it's below them all); `remarks` says when it was snapped.
- The reps go up when the next load is lighter than one full step up (after every set ✅) or than the last set's load (otherwise): + `rep_step` while that stays within the rep range, else + `rep_step_small`.
- Bodyweight with every set ✅: reps + `rep_step` (or `rep_step_small`). Timed holds: + `secs_step` while that stays within the range, else + `secs_step_small`.

```python
>>> from agents.fitness_coach.lib.progression import NeedsPick, next_line
>>> def sets(load, reps, signal="completed"):
...     return [{"load": load, "reps": reps, "signal": signal}] * 3
>>> next_line(squat, sets(135, 6), today, gym, rules)
{'id': 'bb_back_squat', 'last': '2026-10-16', 'load': 135, 'reps': 6, 'next_load': 145}
>>> next_line(squat, [{"load": 125, "reps": 6, "signal": "up"}, {"load": 135, "reps": 5, "signal": "completed"}], today, gym, rules)
{'id': 'bb_back_squat', 'last': '2026-10-16', 'load': 135, 'reps': 5, 'next_load': 135}
```

The bench at 100 × 6, every set ✅: one step up, 105, can't be built. The coach picks the weight below the gap, and the reps go up by 2; a pick of 105 is snapped down the same way. At 102.5 the coach holds until the reps reach the top of the range, then picks 115, back to the range:

```python
>>> next_line(bench, sets(100, 6), today, gym, rules)
Traceback (most recent call last):
...
agents.fitness_coach.lib.progression.NeedsPick: one step up from 100 is 105, which can't be built (nothing loads between 102.5 and 115)
>>> next_line(bench, sets(100, 6), today, gym, rules, pick=102.5)
{'id': 'bb_bench_press', 'last': '2026-10-16', 'load': 100, 'reps': 6, 'next_load': 102.5, 'next_reps': 8}
>>> next_line(bench, sets(100, 6), today, gym, rules, pick=105)["next_load"]
102.5
>>> next_line(bench, sets(102.5, 7), today, gym, rules, pick=102.5)
{'id': 'bb_bench_press', 'last': '2026-10-16', 'load': 102.5, 'reps': 7, 'next_load': 102.5, 'next_reps': 8}
>>> next_line(bench, sets(102.5, 8), today, gym, rules, pick=115)
{'id': 'bb_bench_press', 'last': '2026-10-16', 'load': 102.5, 'reps': 8, 'next_load': 115}
```

At the heaviest dumbbell the load holds and the reps go up, past the top of the range by `rep_step_small`; pull-ups at 7 add 2, at 9 only 1 (the range tops at 10); a 45 s plank adds 10, a 55 s one only 5 (the range tops at 60 s):

```python
>>> goblet = {"id": "db_goblet_squat", "primary_muscle": "Quads", "equipment": ["Dumbbells"],
...           "tracking": "weight_reps", "rep_range": "8-12"}
>>> next_line(goblet, sets(50, 12), today, gym, rules)
{'id': 'db_goblet_squat', 'last': '2026-10-16', 'load': 50, 'reps': 12, 'next_load': 50, 'next_reps': 13}
>>> pull_ups = {"id": "pull_ups", "primary_muscle": "Back", "equipment": ["Pull-Up Bar"], "tracking": "reps", "rep_range": "5-10"}
>>> [next_line(pull_ups, [{"reps": r, "signal": "completed"}] * 3, today, gym, rules)["next_reps"] for r in (7, 9)]
[9, 10]
>>> [next_line(plank, [{"secs": s, "signal": "completed"}] * 3, today, gym, rules)["next_secs"] for s in (45, 55)]
[55, 60]
```

### Recovery after the session

The primary group of each main and accessory lift drops to `worked_recovery` (never up); each group hit only as a secondary, or by a finisher, loses `secondary_hit`, once, down to 0 at most. A squat and a plank, from full recovery:

```python
>>> from agents.fitness_coach.lib.muscle_recovery import after_session
>>> done = [{**squat, "role": "main", "secondary_muscles": ["Glutes", "Hamstrings", "Core"]},
...         {**plank, "role": "finisher", "secondary_muscles": ["Shoulders"]}]
>>> after = after_session({g: 100 for g in values}, done, rules)
>>> {g: after[g] for g in ("Quads", "Glutes", "Core", "Shoulders", "Chest")}
{'Quads': 10, 'Glutes': 70, 'Core': 70, 'Shoulders': 70, 'Chest': 100}
```

### The writes

The history gets one line per exercise, skipped ones included; each progression line is replaced in place (new exercises are appended, unreadable lines kept); muscle_recovery.json is rewritten with `updated` set to now. Each file's write permission is checked before the first write. Then Google Health gets one STRENGTH_TRAINING session from `start` to the end; if that fails, the files stay saved and the result says why.
