---
name: update_schedule
description: Adds or modifies cron schedules in an agent's agent.json using the typed schedule format (kind, precondition, session_policy), validated with schedule_validate before saving.
---

## Overview
This skill guides the agent to safely read, modify, and update the `schedules`
array inside an agent's `agent.json`. Every schedule is typed: a **prompt**
schedule runs an LLM turn, a **script** schedule runs Python scripts from
`scripts/`. The scheduler checks cheaply whether there is work *before* it
spends a single token, and rejects any entry that does not follow this format
(the rejection is posted to the entry's channel and the schedule never runs).

## When to Use
Use this skill whenever a user requests adding a cron job, modifying a scheduled
task, or setting up a recurring automated action for an agent.

## Workflow

### 1. Read Existing Configuration
- Use the `filesystem` tool to `read` the target agent's `agents/<agent_id>/agent.json`.
- Locate the `schedules` array. If it doesn't exist, prepare to initialize it.
- Change only the `schedules` array. Never touch other keys: copy them (`skills`, `thinking`, `voice`, ...) exactly as read, since an `agent.json` that fails validation is not loaded at all.

### 2. Choose the Kind

**`"kind": "prompt"`: an LLM turn on the agent itself.**
```json
{
  "id": "<short-stable-slug>",
  "kind": "prompt",
  "channel": "<target_channel>",
  "thread": "<optional_thread_name>",
  "cron": "<standard_cron_pattern>",
  "precondition": { "type": "<see table>", "...": "..." },
  "session_policy": "stateless",
  "thinking_level": "<optional: minimal | low | medium | high>",
  "priority": "<optional: IGNORE | LOW | DEFAULT | HIGHEST>",
  "label": "<optional: how a voice briefing names a post, e.g. your morning check-in>",
  "prompt": ["<instruction>"]
}
```
- `channel` and `precondition` are required. `cron` runs in local time.
- `session_policy`: `stateless` (default: history is cleared before each run)
  or `persistent` (the scheduled session keeps its history, e.g. an ongoing
  coaching thread). Default to `stateless` unless the task genuinely builds on
  the previous run's conversation.
- `thinking_level` (optional): `minimal` | `low` | `medium` | `high`. Omit it
  and the run uses the agent's scheduled level (`high`, unless the agent's
  `thinking` block overrides `scheduled`). Set a lower level for cheap,
  frequent checks, such as a nudge that only relays what it finds; leave
  synthesis-heavy runs (weekly reviews, research) at the default. PRO models
  have no `minimal`; it is raised to `low`.
- Do **not** write "if empty, terminate silently" in the prompt. That is what
  the precondition is for; by the time the prompt runs, there is work.
- Don't ask for a speech version in the prompt either. Unless the priority is
  `IGNORE`, the run gets the scheduled `<voice_mode>` block and writes one
  itself, with any direct question marked `<question>`.

**`"kind": "script"`: deterministic Python, no LLM.**
```json
{
  "id": "<short-stable-slug>",
  "kind": "script",
  "channel": "<target_channel>",
  "thread": "<optional_thread_name>",
  "cron": "<standard_cron_pattern>",
  "priority": "<optional: IGNORE | LOW | DEFAULT | HIGHEST>",
  "label": "<optional>",
  "script": ["first.py", "second.py --flag value"]
}
```
- `script` is one bare file name in `scripts/` (with optional args), or a list
  run in order.
- Each script must define a module-level `has_work(ctx)` returning a bool or
  `(bool, reason)`, and keep all side effects under
  `if __name__ == "__main__":`. The scheduler checks this by parsing the file
  and rejects scripts that don't comply.
- The script's stdout is posted to the channel; empty stdout posts nothing.
- `session_policy` and `thinking_level` are not allowed on script schedules.

**Voice briefings: `priority` and `label` (any kind).** Each post a schedule
makes puts its channel in the channel inbox, which voice briefings read when
the user joins the voice channel.
- `priority`:
  - `IGNORE`: never in the inbox (housekeeping: syncs, reapers).
  - `LOW`: not in the join digest; "next agent" reaches it last.
  - `DEFAULT`: in the digest. This is the default.
  - `HIGHEST`: also announced live while the user is in voice. Use it rarely.
- `label`: a short noun phrase (60 characters at most), spoken after the agent's
  name: "Marvin has **the market scan**". Without one, the digest says "an update".

### 3. Pick the Precondition (prompt schedules)

**Core preconditions** work for any agent. Each answers "is there work?" from local state: files, a local database, a free read. No model.

| type | parameters | runs when |
|---|---|---|
| `files_exist` | `paths` (globs) | any non-hidden file matches, e.g. an inbox |
| `files_changed_since` | `paths` | something changed since the last successful run |
| `file_matches` | `path`, `pattern` (regex) | the file exists and contains a match |
| `json_non_empty` | `path`, `keys` | any listed key holds a non-empty value |
| `task_list_non_empty` | `filter`: `todo` or `untriaged` | tasks.db has matching open tasks |
| `project_list_non_empty` | `status` (default `executing`) | projects.db has such projects |
| `sleep_logged_today` | none | today's sleep has synced (the user is up) and the schedule hasn't succeeded today |
| `always` | `reason` (required) | every time |

**Scans** are an agent's own code, in `agents/<agent_id>/preconditions/<type>.py`, and only that agent's schedules may use them. A scan checks a domain in code before any model call and briefs the run it starts:

| type | agent | runs when | findings |
|---|---|---|---|
| `market_screen` | market-scout | a watched ticker crossed a threshold in market-scout's RULES.md (`move_pct`, `volume_ratio`, `earnings_days`) in today's session | the screen table, with what each ticker crossed |
| `property_screen` | property-scout | new listings match the `real_estate_scout` criteria | the matches, with their numbers, and criteria.md's lines for judgment |
| `credit_windows` | reward-travel | a card credit newly falls behind or nears its deadline (windows in RULES.md) | those credits, status, days left, period end |
| `ha_audit` | home-steward | Home Assistant has new errors or unavailable entities | the new problems, then counts of ongoing ones |
| `projects_attention` | goal-setter | an executing project needs coaching (weights in RULES.md) | the top projects with reasons, and the first one's context |
| `morning_briefing` | day-planner | always | the morning plan's dates, chores, tasks and notes |
| `dinner_checkin` | meal-planner | tonight's dinner isn't logged yet | none |

**Choosing.**
- A core type when a file or database state answers the question. A scan when it takes domain logic: thresholds, dates, or the read-only call the agent's first step would make anyway.
- Declare a scan like any precondition, `{"type": "market_screen"}`, with optional `"args": {...}`; its module says what it takes.
- A scan that fires appends its findings to the prompt as `<findings source="<type>">`. Write the prompt to work from them, never to re-read what the scan read.
- Scans read their thresholds from the agent's RULES.md. To change when one fires, change RULES.md, not the schedule.
- For a scheduled check, prefer a scan to `always`: quiet days then cost no model call.
- A new scan is code: pick from the agent's existing scans, and ask for a new one with `workflow_change`. Until it lands, use `always` and tell the user which scan would gate it.

Use `always` only when the work cannot be detected locally (external APIs,
date-driven briefings, user check-ins), and state that reason honestly.
Relative paths resolve from the repo root (`pkm/...`).

**Scan problem notices.** A scan never skips quietly over a problem:
- When it can't read its data, or sees issues on a quiet day, it skips the run and the scheduler posts one short notice in the schedule's channel, once per issue per day. The user replies there, and the agent has the notice in context.
- On a day with work, the issues ride along in the findings. Write the prompt so the agent tells the user, in one line, what couldn't be read.
- A network error is only logged; the next run retries.
- Never add prompt text to report a failed or empty check: the scheduler does it.

**Gates on a file.** For work that follows one file (a memory topic, a log, a queue), gate it with `files_changed_since` on that file, e.g. `pkm/wiki/memory/topics/<topic>.md`: a quiet week costs nothing.
- It fires on the first run (no successful run yet), then only when something under its paths changed since the last successful run.
- A file that doesn't exist yet doesn't fire after the first run; it starts firing once the file is written.
- Its paths may be globs; a folder counts every file in it, deletions included.

### 4. Validate, Then Write
- Call `schedule_validate` with the `agent_id` and the full proposed
  `schedules` array as `schedules_json`. Fix every reported problem, then
  validate again until every entry is OK.
- Insert or update the entry in the `schedules` array and save it with the
  `filesystem` tool's `overwrite` action to `agents/<agent_id>/agent.json`.
- Schedules hot-reload; no restart is needed.
- Run `agent_lint` on the agent and fix what it finds before reporting done.

## Required Tools
- `filesystem`: to read `agent.json` and overwrite it with the updated `schedules` list, and to read `scripts/` and the agent's scans in `agents/<agent_id>/preconditions/`.
- `schedule_validate`: to check the proposed entries before saving.
- `agent_lint`: to check the agent once the schedule is saved.