---
name: coach_home_workout
description: File map, schemas and rules for planning, serving and logging home workouts from pkm/wallet/exercise (exercise library, loadable weights, weight progression, workout history, muscle recovery).
---
## Overview
Use this skill for every workout session. It explains the data files under `pkm/wallet/exercise/`, how to pick exercises, how to prescribe and progress weights from Alva's history, how to send exercise media to Discord cheaply, and exactly what to write back at the end.

## Data Files (all under `pkm/wallet/exercise/`)
| File | Contents | Access |
|---|---|---|
| `muscle_recovery.json` | Recovery % per muscle group + `updated` timestamp | read; overwrite at wrap-up |
| `equipment.md` | Loadable weights, dumbbell settings, setup, stations. No working weights. | read only |
| `library/<group>.json` | Exercise library, one file per muscle group (200 exercises) | read only |
| `progression.jsonl` | One line per exercise done: last result and the next target | grep; replace_block / append |
| `workout_history.jsonl` | Append-only log, one line per exercise per session | append only |
| `assets/` | Exercise images (GIF photo loops, JPG/PNG start/end illustrations) | never read; reference by path |

Muscle groups (exact keys, also the library file names in lowercase): Chest, Back, Shoulders, Biceps, Triceps, Quads, Hamstrings, Glutes, Calves, Core.

## Exercise Library (`library/<group>.json`)
A JSON array, one exercise per line:
```json
{"id": "bb_back_squat", "name": "Barbell Back Squat", "primary_muscle": "Quads", "secondary_muscles": ["Glutes", "Hamstrings", "Core"], "equipment": ["Barbell", "Rack"], "role": "main", "tracking": "weight_reps", "rep_range": "5-8", "level": "intermediate", "cue": "Set safeties. Brace, sit between your hips, drive the floor away.", "media": "pkm/wallet/exercise/assets/bb_back_squat.gif"}
```
- `role`: `main` = heavy compound lift (first slot), `accessory` = supporting work, `finisher` = core, carries, conditioning (last slot).
- `tracking`: `weight_reps` (log load + reps), `reps` (bodyweight; log reps), `time` (log seconds).
- `rep_range`: default per set. `/leg` or `/side` means per side. `level`: beginner | intermediate | advanced.
- Only read the files for the groups you need, all in one `filesystem` call (max 10 instructions per call).

### Picking a session (~30 min)
1. Groups at 100% current recovery are eligible (formula under Muscle Recovery).
2. Pick 1 `main` + 2 `accessory` + 1 `finisher`, 3 sets each, from 1–2 stations in `equipment.md`.
3. Keep the same `main` lifts for 4–6 weeks so loads can progress; rotate accessories and finishers for variety.
4. Sleep < 6h: drop one exercise or one set per exercise.
5. Prefer `beginner`/`intermediate` exercises unless progression shows the easier version is mastered.

## Loads (`equipment.md`)
- Barbell load = total including the 45 lb bar. Use only the "Loadable totals" ranges; 85–92.5 and 225–232.5 lb cannot be built.
- Dumbbells = weight per hand, only the listed dial settings.
- Landmine = plate weight on the sleeve. Lever Arms = plate weight per arm.
- Rings, TRX, bodyweight: progress reps, tempo or body angle instead of load.

## Weight Progression (`progression.jsonl`)
One line per exercise, written by you at wrap-up:
```json
{"id":"bb_back_squat","last":"2026-10-13","load":185,"reps":6,"next_load":195}
{"id":"pull_ups","last":"2026-10-13","reps":7,"next_reps":8}
{"id":"plank","last":"2026-10-13","secs":45,"next_secs":50}
```
**Reading (setup):** one `filesystem` call with a `grep` per planned exercise, using the quoted id as `search_string`, e.g. `"bb_back_squat"` (quotes included, so `side_plank` never matches `plank`). A hit looks like `progression.jsonl:3: {...}`; keep the JSON after `3: ` exactly, you need it to update the line later. "No matches" means first time.

**Prescribing:**
- Line found → prescribe `next_load` (or `next_reps` / `next_secs`) with the library `rep_range`.
- `last` more than 21 days ago → start 10% lighter (snap to a loadable weight).
- No line (first time) → calibrate: start light (empty bar, light dumbbells, easy angle), climb with 🔼 set by set; the last completed set becomes the baseline. Alva never types weights.

**Updating (wrap-up), per exercise done:**
- All sets ✅ at the prescribed load → `next_load` = load + one step:
  - Barbell: +5 lb upper body, +10 lb lower body (Quads, Hamstrings, Glutes, Calves and deadlift variants).
  - Dumbbells: next dial setting. Landmine / Lever Arms: +5 lb.
  - `reps`: +1–2 reps. `time`: +5–10 s.
- Any 🔼 or 🔽 → the last set's load becomes `load` and `next_load` (repeat it next time).
- Snap `next_load` to a loadable weight. If it falls in a gap, keep the weight below and set `next_reps` 1–2 above the last reps instead.
- Skipped exercise → leave its line untouched.
- Existing line → `replace_block` with `old_block` = the exact line from setup and `new_block` = the new line.
- New exercise → `append` the new line followed by a newline.
- Never `overwrite` this file.

## Workout History (`workout_history.jsonl`)
Append one line per exercise performed (skipped exercises included, with `"skipped": true` and no sets), each ending with a newline:
```json
{"date":"2026-10-13","session":"2026-10-13T07:30","id":"bb_back_squat","sets":[{"load":175,"reps":6,"signal":"completed"},{"load":175,"reps":6,"signal":"up"},{"load":185,"reps":6,"signal":"completed"}]}
```
- `session` = local start time `YYYY-MM-DDTHH:MM`, the same for every line of one workout.
- `signal` = the button tapped after that set: `completed` | `up` | `down`. Timed sets use `secs` instead of `reps`; bodyweight sets omit `load`.
- Never rewrite or delete history lines.

## Muscle Recovery (`muscle_recovery.json`)
```json
{"updated": "2026-10-13T08:05:00-07:00", "Chest": 100, "Back": 10, "Shoulders": 100, "...": 100}
```
- Current recovery of a group = min(100, stored value + 2 × hours since `updated`), so a fully worked muscle is back to 100 in about 2 days.
- Wrap-up: compute current values for every group, then set primary muscles of `main`/`accessory` lifts to 10, subtract 30 (floor 0) from groups hit only as secondary or by finishers, set `updated` to now, and `overwrite` the file with all 10 groups plus `updated`.

## Serving Media in Discord
- Send the exercise image by putting its `media` path, unchanged, in an images block: `<images><image path="pkm/wallet/exercise/assets/bb_back_squat.gif"/></images>`.
- The orchestrator strips that block and uploads the file to Discord as an attachment. Only the path costs tokens.
- NEVER `read` or `read_image` files in `assets/`: that pulls the image into your context and wastes tokens.
- GIFs loop real start/end photos. JPG/PNG files are START/END illustrations. If an image is missing, send the `cue` alone.

## Example
User taps "start workout". `muscle_recovery.json` (after the formula) shows Quads, Glutes and Core at 100.
1. One `filesystem` call: read `equipment.md`, `library/quads.json`, `library/glutes.json`, `library/core.json`.
2. Picks (Rack + Floor stations): Barbell Back Squat (main), Barbell Hip Thrust (accessory), Bulgarian Split Squat (accessory), Ab Wheel Rollout (finisher).
3. One `filesystem` call: grep `progression.jsonl` for `"bb_back_squat"`, `"bb_hip_thrust"`, `"db_bulgarian_split_squat"`, `"ab_wheel_rollout"`.
4. Plan message: "Squat 3×5-8 @ 185 · Hip Thrust 3×8-12 @ 135 · Bulgarian Split Squat 3×8-12/leg @ 25s · Ab Wheel 3×8-12", then the `[💪 Let's Go]` poll.

## Boundaries
- Never write working weights into `library/*.json` or `equipment.md`, and never edit the library or `assets/`.
- Keep messages short: name, sets × reps @ load, one cue, image, poll.
- If a write fails on permissions, say so in one line; don't retry with other paths.

## Required Tools
- `filesystem`: read `pkm/wallet/exercise/` (library, equipment, recovery); grep, replace_block and append on `progression.jsonl`; append to `workout_history.jsonl`; overwrite `muscle_recovery.json`.
- `google_health`: read recent sleep to adjust volume, and log the finished session as exercise.
