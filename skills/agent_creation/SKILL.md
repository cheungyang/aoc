---
name: agent_creation
description: Skill for creating new agents with required files and vault structures.
---
## Overview
This skill guides agents on how to create a new agent in the system, including workspace files and Obsidian vault folders.

## Workflow

### Prerequisite
Before triggering this skill, the agent MUST have a good understanding of the human's intention and the specific function of the new agent being created.

### 1. Create Workspace Directory
Create a new directory for the agent under `agents/<agent_id>/`.

### 2. Create Agent Configuration
Create `agents/<agent_id>/agent.json`. Follow the patterns from `agents/main/agent.json` but do NOT copy agent-specific values (like `agent_id`, `name`, `discord_token_key`, `channel_hosts`) unless they are intended for the new agent.

### 3. Create Core Markdown Files
Create the following files under `agents/<agent_id>/`:
- **SOUL.md**: Defines who the agent is (persona, tone, boundaries). It ensures the agent acts as a consistent partner rather than a generic bot.
- **AGENTS.md**: Defines how the agent operates. It contains operating instructions, rules, and priorities for the session.
- **USER.md**: Stores information about the human user and how the agent should address them.

### 4. Create Obsidian Vault Folders
Create the following folders in the Obsidian vault (assumed path `pkm-oc/`):
- `pkm-oc/agents/<agent_id>/memory`
- `pkm-oc/agents/<agent_id>/feedback`
- `pkm-oc/daily-summary/<agent_id>`

### 5. Create Initial Vault Files
Create the following markdown files in the vault:
- `pkm-oc/agents/<agent_id>/memory/memory.md` (for long-term, curated memory).
- `pkm-oc/agents/<agent_id>/feedback/feedback.md` (for long-term, curated feedback from human).

## Required Tools
To execute these steps, the agent needs the `filesystem` tool to read/write files and create directories (folding `mkdir` into `write`). The agent must check its permissions in `agent.json` before attempting operations.
