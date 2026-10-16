---
name: coach_home_workout
description: File map, schemas and rules for planning, serving and logging home workouts from pkm/wallet/exercise (exercise library, loadable weights, weight progression, workout history, muscle recovery).
---
All data lives in `pkm/wallet/exercise/`. Muscle groups (exact keys; lowercase = library file names): Chest, Back, Shoulders, Biceps, Triceps, Quads, Hamstrings, Glutes, Calves, Core.

| File | Contents |
|---|---|
| `muscle_recovery.json` | `{"updated": "2026-10-13T08:05:00-07:00", "Chest": 100, "Back": 10, ...}`, all 10 groups |
| `equipment.md` | Loadable weights, dumbbell dials, setup, stations |
| `library/<group>.json` | Exercises: a JSON array, one exercise per line (below) |
| `progression.jsonl` | One line per exercise done: last result, next target |
| `workout_history.jsonl` | One line per exercise per session |
| `assets/` | Images: GIF photo loops, JPG/PNG start/end illustrations |

Library line: `{"id":"bb_back_squat","name":"Barbell Back Squat","primary_muscle":"Quads","secondary_muscles":["Glutes","Hamstrings","Core"],"equipment":["Barbell","Rack"],"role":"main","tracking":"weight_reps","rep_range":"5-8","level":"intermediate","cue":"Brace, sit between your hips.","media":"pkm/wallet/exercise/assets/bb_back_squat.gif"}`
- `role`: `main` = heavy compound (first slot), `accessory` = supporting work, `finisher` = core, carries, conditioning (last slot).
- `tracking`: `weight_reps` (load + reps), `reps` (bodyweight), `time` (seconds). `rep_range` is per set; `/leg` or `/side` means per side.

## 1. Setup Phase (user starts a workout, or the morning push)
1. In one step: `read` `muscle_recovery.json` and `equipment.md`, and get last night's sleep from `google_health`. Current recovery = min(100, stored + 2 × hours since `updated`); groups at 100 are eligible.
2. `read` `library/<group>.json` for the eligible groups you'll pick from, in one `filesystem` call (max 10 instructions per call; use a second call in the same step for more).
3. Pick 1 `main` + 2 `accessory` + 1 `finisher`, 3 sets each (~30 min), from 1–2 stations. Keep the same `main` lifts for 4–6 weeks; rotate the rest. Prefer `beginner`/`intermediate` unless progression shows the easier version is mastered. Sleep < 6 h: drop one exercise or one set per exercise.
4. One `filesystem` call: `grep` `progression.jsonl` per pick with the quoted id as `search_string`, e.g. `"bb_back_squat"` (quotes keep `plank` from matching `side_plank`). A hit is `progression.jsonl:3: {...}`: keep the JSON after `3: ` exactly for wrap-up. No match = first time.
5. Prescribe `next_load` (or `next_reps` / `next_secs`) with the library `rep_range`. `last` over 21 days ago: 10% lighter. First time: start light (empty bar, light dumbbells, easy angle) and climb with 🔼 set by set; the last completed set is the baseline. Alva never types weights.
6. Send the plan in one line (`Squat 3×5-8 @ 185 · Hip Thrust 3×8-12 @ 135 · Ab Wheel 3×8-12`) and a `<poll>` with one `[💪 Let's Go]` button.

## 2. Execution Phase
1. One exercise at a time, kept short: name, sets × reps @ load, its `cue`, and `<images><image path="{media}"/></images>` with `media` unchanged (the file is uploaded for you). Never `read`/`read_image` files in `assets/`. No image: send the cue alone.
2. After each set, a `<poll>`: `[✅ Completed as planned]` `[🔼 Up weight next set]` `[🔽 Down weight next set]` `[⏭️ Skip Exercise]`. 🔼/🔽 move to the next loadable weight; for `reps`/`time` tracking, adjust reps or seconds.
3. Track each set (load, reps, button) in the conversation; write nothing until wrap-up.

## 3. Wrap-up Phase (all exercises done; every write in one step)
1. `muscle_recovery.json`: compute every group's current value, set primaries of `main`/`accessory` lifts to 10, subtract 30 (floor 0) from groups hit only as secondary or by finishers, set `updated` to now, and `overwrite` with all 10 groups.
2. `workout_history.jsonl`: `append` one line per exercise, each ending in a newline; skipped ones get `"skipped": true` and no sets. `{"date":"2026-10-13","session":"2026-10-13T07:30","id":"bb_back_squat","sets":[{"load":175,"reps":6,"signal":"up"},{"load":185,"reps":6,"signal":"completed"}]}`. `session` = local start time, the same on every line of a workout; `signal` = the button after that set (`completed` | `up` | `down`); timed sets use `secs` instead of `reps`; bodyweight sets omit `load`. Never rewrite or delete lines.
3. `progression.jsonl`, per exercise done (skipped: leave its line alone). Lines: `{"id":"bb_back_squat","last":"2026-10-13","load":185,"reps":6,"next_load":195}`, `{"id":"pull_ups",...,"reps":7,"next_reps":8}`, `{"id":"plank",...,"secs":45,"next_secs":50}`.
   - All sets ✅ at the prescribed load: one step up. Barbell +5 lb upper body, +10 lb lower (Quads, Hamstrings, Glutes, Calves, deadlift variants); dumbbells next dial; Landmine / Lever Arms +5 lb; `reps` +1–2; `time` +5–10 s.
   - Any 🔼/🔽: the last set's load is both `load` and `next_load`.
   - Snap to a loadable weight; in a gap, keep the weight below and set `next_reps` 1–2 above the last reps.
   - Existing line: `replace_block` with `old_block` = the line from setup. New exercise: `append` the line plus a newline. Never `overwrite` this file.
4. Log the session to `google_health` as exercise.
5. Send a short completion summary.

## Loads (`equipment.md`)
- Barbell = total including the 45 lb bar; only the "Loadable totals" ranges (85–92.5 and 225–232.5 lb can't be built). Any computed load (e.g. 10% lighter) snaps to a loadable weight.
- Dumbbells = per hand, listed dials only. Landmine = plate weight on the sleeve. Lever Arms = plate weight per arm.
- Rings, TRX, bodyweight: progress reps, tempo or body angle.

## Boundaries
- If a write fails on permissions, say so in one line; don't retry with other paths.
