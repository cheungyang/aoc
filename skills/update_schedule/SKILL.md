---
name: update_schedule
description: Adds or modifies cron schedules within an agent's agent.json file, supporting both script-executor syntax and LLM prompt formats.
---

## Overview
This skill guides the agent to safely read, modify, and update the `schedules` array inside an agent's `agent.json` configuration file. It supports two flavors of schedule prompts: one for the non-LLM `script-executor` and one for standard LLM agents.

## When to Use
Use this skill whenever a user requests adding a cron job, modifying a scheduled task, or setting up a recurring automated action for an agent.

## Workflow

### 1. Read Existing Configuration
- Use the `filesystem` tool to `read` the target agent's `agents/<agent_id>/agent.json`.
- Parse the JSON to locate the `schedules` array. If it doesn't exist, prepare to initialize it.

### 2. Determine Schedule Flavor
Determine the target agent to format the `prompt` correctly:

**Flavor 1: script-executor**
Each item in the `prompt` array must be a separate deterministic instruction.
- Syntax for tools: `tool <tool_name> <tool_arguments_in_escaped_json_format>`
- Syntax for scripts: `script <script_name>` (Script must exist in the `scripts/` directory).
*Example:* `["script software-orchestrator.py"]`

**Flavor 2: Standard LLM Agent**
The `prompt` array contains a standard natural language prompt.
*Example:* `["Review all closed PRs for the day and draft a release summary."]`

### 3. Construct the Payload
Create the schedule JSON object:
```json
{
  "channel": "<target_channel>",
  "thread": "<optional_thread_name_if_specified>",
  "cron": "<standard_cron_pattern>",
  "prompt": [
    "<instruction_or_prompt>"
  ]
}
```

### 4. Update and Write
- Insert or update the constructed schedule object inside the `schedules` array of the agent's configuration.
- Use the `filesystem` tool's `overwrite` action to save the updated JSON structure back to `agents/<agent_id>/agent.json`.

## Required Tools
- `filesystem`: Required to read the existing `agent.json` configuration and overwrite it with the updated `schedules` list.