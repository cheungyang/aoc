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
- Change only the `schedules` array. Never touch other keys.

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
  "prompt": ["<instruction>"]
}
```
- `channel` and `precondition` are required.
- `session_policy`: `stateless` (default: history is cleared before each run)
  or `persistent` (the scheduled session keeps its history, e.g. an ongoing
  coaching thread). Default to `stateless` unless the task genuinely builds on
  the previous run's conversation.
- Do **not** write "if empty, terminate silently" in the prompt. That is what
  the precondition is for; by the time the prompt runs, there is work.

**`"kind": "script"`: deterministic Python, no LLM.**
```json
{
  "id": "<short-stable-slug>",
  "kind": "script",
  "channel": "<target_channel>",
  "thread": "<optional_thread_name>",
  "cron": "<standard_cron_pattern>",
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
- `session_policy` is not allowed on script schedules.

### 3. Pick the Precondition (prompt schedules)

| type | parameters | runs when |
|---|---|---|
| `files_exist` | `paths` (globs) | any non-hidden file matches, e.g. an inbox |
| `files_changed_since` | `paths` | something changed since the last successful run |
| `file_matches` | `path`, `pattern` (regex) | the file exists and contains a match |
| `json_non_empty` | `path`, `keys` | any listed key holds a non-empty value |
| `task_list_non_empty` | `filter`: `todo` or `untriaged` | tasks.db has matching open tasks |
| `project_list_non_empty` | `status` (default `executing`) | projects.db has such projects |
| `always` | `reason` (required) | every time |

Use `always` only when the work cannot be detected locally (external APIs,
date-driven briefings, user check-ins), and state that reason honestly.
Relative paths resolve from the repo root (`pkm/...`).

### 4. Validate, Then Write
- Call `schedule_validate` with the `agent_id` and the full proposed
  `schedules` array as `schedules_json`. Fix every reported problem, then
  validate again until every entry is OK.
- Insert or update the entry in the `schedules` array and save it with the
  `filesystem` tool's `overwrite` action to `agents/<agent_id>/agent.json`.
- Schedules hot-reload; no restart is needed.

## Required Tools
- `filesystem`: to read `agent.json` and overwrite it with the updated `schedules` list.
- `schedule_validate`: to check the proposed entries before saving.