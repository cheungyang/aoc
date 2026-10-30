---
name: coach_home_workout
description: Plans, serves and logs ~30-minute home workouts with workout_setup and log_workout_session, which compute recovery, loads and progression from pkm/wallet/exercise.
---
`role`: `main` = heavy compound (first slot), `accessory` = supporting work, `finisher` = core, carries, conditioning (last slot). `rx` is per set; `/leg` or `/side` means per side. The user never types weights.

## 1. Setup Phase (user starts a workout, or the morning push)
1. Call `workout_setup()`, asking for `medium` thinking.
2. Pick 1 `main` + 2 `accessory` + 1 `finisher`, 3 sets each (~30 min), from 1–2 stations. Keep the same `main` lifts for 4–6 weeks (`recent_mains`); rotate the rest (`days` since last done). Prefer `beginner`/`intermediate` unless progression shows the easier version is mastered. `short_sleep`: drop one exercise or one set per exercise.
3. Prescribe each pick's `rx`, or its break discount (Choosing loads). `(new)`: start light and climb with 🔼 set by set; the last completed set is the baseline.
4. Send the plan in one line with library names (`Barbell Back Squat 3×5-8 @ 185 · Barbell Hip Thrust 3×8-12 @ 135 · Ab Wheel Rollout 3×8-12`) and a `<poll>` with one `[Let's Go]` button.

## 2. Execution Phase
On 💪 Let's Go, call `workout_setup(ids=[...])` once with the picks: it returns the session `start` and each pick's `cue`, `media`, `loads` and any `gap`.
1. One exercise at a time, kept short: name, sets × reps @ load, its `cue`, and `<images><image path="{media}"/></images>` with `media` unchanged (the file is uploaded for you). Never `read`/`read_image` files in `assets/`. No image: send the cue alone.
2. After each set, a `<poll>`: `[✅ Completed as planned]` `[🔼 Up weight next set]` `[🔽 Down weight next set]` `[⏭️ Skip Exercise]`. 🔼/🔽 move to the next weight in `loads`; for `reps`/`time` tracking, adjust reps or seconds. Rings, TRX, bodyweight: progress reps, tempo or body angle.
3. Track each set (load, reps, button) in the conversation; write nothing until wrap-up.

## 3. Wrap-up Phase (all exercises done)
1. One `log_workout_session` call: every exercise with its sets and buttons (skipped ones `skipped: true`), `start` from Let's Go, `duration_minutes` from `start` to the last set, and `next_load` where the gap rule applies (Choosing loads).
2. Send a short completion summary from its result: sets done and next target per exercise; one line if Google Health wasn't logged.

## Choosing loads
The tools compute the numbers and snap your pick to a buildable weight; these choices are yours.
- **After a break**: an `rx` ending in `(-N%: …)` hasn't been done for weeks. Prescribe the figures after the colon: strength fades over a break, and 🔼 climbs back. Keep the plain `rx` only if the user says they trained elsewhere meanwhile.
- **Weight gaps**: a pick's `gap` means one step up from `from` can't be built: nothing loads between `below` and `above`. When every set at `from` was ✅, pass `next_load`:
  - `from` under `below`: `below` (the tool adds reps);
  - `from` at `below`: `below` again until the reps done reach `top_reps`, then `above`.

## Boundaries
- If a write fails on permissions, say so in one line; don't retry with other paths.
