---
name: agent_creation
description: Skill for creating new agents with required files and vault structures, outputting standardized IPC XML.
---
## Overview
This skill guides agents on how to create a new agent in the system, including workspace files and Obsidian vault folders. It concludes by outputting a standardized Inter-Process Communication (IPC) XML block.

## When to Use
Use this skill exclusively when the user explicitly requests the creation of a new agent persona.

## Boundaries & Guardrails
- **Strict Approval**: NEVER create an agent or write configuration files without first presenting a detailed plan (covering agent purpose, tools, and SOUL) and receiving explicit user approval.
- **Identity Consistency**: Ensure the `SOUL.md` strongly aligns with the assigned description and tools in `agent.json`.
- **System Isolation**: Do not modify existing agents or system files during the creation of a new agent.
- **Formatting**: The final output MUST strictly adhere to the requested IPC XML structure.

## Workflow

### 1. Create Workspace Directory
Create a new directory for the agent under `agents/<agent_id>/`.

### 2. Create Agent Configuration
Create `agents/<agent_id>/agent.json`. You MUST use the exact JSON structure template below:
```json
{
  "agent_id": "<agent_id>",
  "channels": ["<Channels where this agent could be active>"],
  "name": "<Agent Name>",
  "emoji": "<Emoji>",
  "description": "<Short Description>",
  "model": "<Optional Tier: FLASH_LITE | FLASH | PRO>",
  "provider": "<Optional: google (default) | local>",
  "discord_token_key": "<Optional Discord Token Key>",
  "channel_hosts": ["<Optional Channel>"],
  "tools": {
    "<tool_name_1>": {}
  },
  "skills": []
}
```
*Critical Constraints for agent.json:*
- The `tools` field MUST be a dictionary object (e.g., `"tools": { "tool_name": {} }`), NOT an array.
- Do not explicitly add tools to the `tools` object if they are already inherited via the skills listed in the `skills` array (e.g., `filesystem` permissions are inherited via the `dream` skill).
- Omit `schedules` unless the agent needs recurring work. To add schedules, load the `update_schedule` skill: every entry needs a `kind` and, for prompt schedules, a `precondition`, and it must pass `schedule_validate` before it is saved. Invalid entries are rejected at load and never run.
- The `model` field names a **tier**, never a version string like `gemini-3.5-flash-lite` or an
  on-device build id. This holds for every provider. Valid tiers are defined per provider in
  `core/util/models.py`; omit the field to accept the default. Choose `FLASH_LITE` for narrow,
  high-volume work, `FLASH` for everyday reasoning, and `PRO` only when the agent genuinely
  needs the strongest model.
- The `provider` field selects where the model runs. Omit it for Gemini. `"local"` routes the
  agent to the on-device server configured by `LOCAL_LLM_BASE_URL`. Because both providers
  answer the same tier names, moving an agent on-device is adding this one key and nothing
  else — its `model` line is untouched.

*Before setting `"provider": "local"`, know two things:*
- **The server handles one request at a time.** Local agents queue behind each other, so keep
  the local roster small and prefer low-volume, single-purpose agents.
- **Never put a mid-turn caller on it.** Components that invoke a model *inside* another turn —
  `graph-worker-low` (used by `ContextPruner`) and the voice verbalizer — would queue behind the
  very turn they are serving, stalling it. These stay on Gemini.


### 3. Create Core Markdown Files
Create the following files under `agents/<agent_id>/`:
- **SOUL.md**: Defines who the agent is (persona, tone, boundaries). It ensures the agent acts as a consistent partner rather than a generic bot.
- **AGENTS.md**: Defines how the agent operates. It contains operating instructions, rules, and priorities for the session.
- **USER.md**: Stores information about the human user and how the agent should address them.
- **IDENTITY.md**: Stores basic identity information (Agent ID, Name, Emoji, Description).

#### Token budget — treat this as a design constraint

Every one of these files is concatenated into the system prompt on **every
single turn, forever**. A paragraph written once is paid for thousands of times.

Target sizes when creating a new agent:

| File | Target | Hard ceiling |
|---|---:|---:|
| `AGENTS.md` | 1500 chars | 2500 |
| `SOUL.md` | 1000 chars | 1500 |
| `USER.md` | 400 chars | 800 |
| `IDENTITY.md` | 250 chars | 400 |
| `INSTRUCTIONS.md` (if used) | 0 | 2500 |

**Total authored persona should be under 3500 characters (~875 tokens).**

Why it is worth being strict:

- Memory is appended to the same prompt: the shared Profile, the topics in
  `memory_topics`, and the agent's own `MEMORY.md` / `FEEDBACK.md`. The dream
  keeps each within a budget, but together they add up to ~2,000 tokens for an
  active agent. Whatever you author sits on top of that.
- Measured across the current roster, the largest agents reach ~4,500 tokens of
  system prompt plus ~2,700 of tool schemas. The smallest do the same class of
  work at ~800 + 636.
- An agent intended to run on-device (`"provider": "local"`) must fit its whole
  prompt, tool schemas *and* conversation inside the server's window, commonly
  4096 tokens. Agents above ~2000 tokens of static prompt cannot run locally at
  all.

Practical rules:

- Prefer one precise sentence to three hedging ones. Do not restate the same
  rule in both `SOUL.md` and `AGENTS.md`.
- Do not paste examples into the persona if a skill can carry them — skills load
  on demand, the persona never unloads.
- Put durable behaviour in the authored files and let the dream cycle own
  memory. Never pre-seed the vault files. Facts about the user belong in the
  shared Profile/topics, not in `USER.md`.
- Set `memory_topics` in `agent.json` to the shared topics (from
  `pkm/wiki/memory/TAGS.md`) the agent needs to read. List each one; subscribe
  sparingly, since every topic costs prompt on every turn.
- Fewer tools is also a prompt saving: each bound tool schema costs 130–550
  tokens on every turn.

### 4. Create Obsidian Vault Folders
Create the following folders in the Obsidian vault (assumed path `pkm/`):
- `pkm/agents/<agent_id>/memory_logs`

### 5. Create Initial Vault Files
Create the following markdown files in the vault:
- `pkm/agents/<agent_id>/MEMORY.md` (for long-term, curated memory).
- `pkm/agents/<agent_id>/FEEDBACK.md` (for long-term, curated feedback from human).

### 6. Agent-Friendly Output & Memory (IPC Format)
Once the files and directories are successfully written, finalize the execution by outputting the strict XML structure below. This format is required to ensure perfect readability for routing agents.
```xml
<agent_creation_response>
  <original_request>[The user's vision or request for the new agent]</original_request>
  <triggering_agent>[Agent ID or 'User']</triggering_agent>
  <payload>
    <agent_id>[The ID of the generated agent]</agent_id>
    <files_created>
      - agents/<agent_id>/agent.json
      - agents/<agent_id>/SOUL.md
      - agents/<agent_id>/AGENTS.md
      - agents/<agent_id>/USER.md
      - agents/<agent_id>/IDENTITY.md
      - pkm/agents/<agent_id>/MEMORY.md
      - pkm/agents/<agent_id>/FEEDBACK.md
    </files_created>
    <capabilities>[Brief summary of the agent's specialization and assigned tools/skills]</capabilities>
  </payload>
  <errors>[Any filesystem errors encountered, or 'None']</errors>
  <learnings>[Execution insights on agent persona design, context window optimization, or user preferences]</learnings>
</agent_creation_response>
```
**Memory Trigger**: Immediately after outputting the XML, use the `memory` skill to record the contents of the `<learnings>` tag so the system learns from this execution.

## Required Tools
- `filesystem`: Required to read reference files and write/overwrite configuration files in both `agents/<agent_id>/` and `pkm/agents/<agent_id>/`.