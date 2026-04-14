---
name: agent_creation
description: Skill for creating new agents with required files and vault structures.
---
## Overview
This skill guides agents on how to create a new agent in the system, including workspace files and Obsidian vault folders.

## When to Use
Use this skill exclusively when the user explicitly requests the creation of a new agent persona.

## Boundaries & Guardrails
- **Strict Approval**: NEVER create an agent or write configuration files without first presenting a detailed plan (covering agent purpose, tools, and SOUL) and receiving explicit user approval.
- **Identity Consistency**: Ensure the `SOUL.md` strongly aligns with the assigned description and tools in `agent.json`.
- **System Isolation**: Do not modify existing agents or system files during the creation of a new agent.

## Workflow

### 1. Create Workspace Directory
Create a new directory for the agent under `agents/<agent_id>/`.

### 2. Create Agent Configuration
Create `agents/<agent_id>/agent.json`. You MUST use the exact JSON structure template below:
```json
{
  "agent_id": "<agent_id>",
  "name": "<Agent Name>",
  "emoji": "<Emoji>",
  "description": "<Short Description>",
  "model": "<Optional Model Name>",
  "discord_token_key": "<Optional Discord Token Key>",
  "channel_hosts": ["<Optional Channel>"],
  "tools": {
    "<tool_name_1>": {}
  },
  "skills": ["memory", "dream"]
}
```
*Critical Constraints for agent.json:*
- The `tools` field MUST be a dictionary object (e.g., `"tools": { "tool_name": {} }`), NOT an array.
- Do not explicitly add tools to the `tools` object if they are already inherited via the skills listed in the `skills` array (e.g., `obsidian` is inherited via the `memory` skill).

### 3. Create Core Markdown Files
Create the following files under `agents/<agent_id>/`:
- **SOUL.md**: Defines who the agent is (persona, tone, boundaries). It ensures the agent acts as a consistent partner rather than a generic bot.
- **AGENTS.md**: Defines how the agent operates. It contains operating instructions, rules, and priorities for the session.
- **USER.md**: Stores information about the human user and how the agent should address them.
- **IDENTITY.md**: Stores basic identity information (Agent ID, Name, Emoji, Description).

### 4. Create Obsidian Vault Folders
Create the following folders in the Obsidian vault (assumed path `pkm/`):
- `pkm/agents/<agent_id>/memory_logs`

### 5. Create Initial Vault Files
Create the following markdown files in the vault:
- `pkm/agents/<agent_id>/MEMORY.md` (for long-term, curated memory).
- `pkm/agents/<agent_id>/FEEDBACK.md` (for long-term, curated feedback from human).
- `pkm/agents/<agent_id>/CONTEXT.md` (for long-term, curated context about the human).

## Required Tools
- `filesystem`: Required to read reference files and write/overwrite configuration files in both `agents/<agent_id>/` and `pkm/agents/<agent_id>/`.