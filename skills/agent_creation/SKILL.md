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
Create `agents/<agent_id>/agent.json`. Follow the patterns from existing agents but do NOT copy agent-specific values (like `agent_id`, `name`, `discord_token_key`, `channel_hosts`) unless they are intended for the new agent. Ensure required skills (like `memory` and `dream`) are included in the skills array.

### 3. Create Core Markdown Files
Create the following files under `agents/<agent_id>/`:
- **SOUL.md**: Defines who the agent is (persona, tone, boundaries). It ensures the agent acts as a consistent partner rather than a generic bot.
- **AGENTS.md**: Defines how the agent operates. It contains operating instructions, rules, and priorities for the session.
- **USER.md**: Stores information about the human user and how the agent should address them.
- **IDENTITY.md**: Stores basic identity information (Agent ID, Name, Emoji, Description).

### 4. Create Obsidian Vault Folders
Create the following folders in the Obsidian vault (assumed path `pkm-oc/`):
- `pkm-oc/agents/<agent_id>/memory_logs`

### 5. Create Initial Vault Files
Create the following markdown files in the vault:
- `pkm-oc/agents/<agent_id>/MEMORY.md` (for long-term, curated memory).
- `pkm-oc/agents/<agent_id>/FEEDBACK.md` (for long-term, curated feedback from human).
- `pkm-oc/agents/<agent_id>/CONTEXT.md` (for long-term, curated context about the human).

## Required Tools
- `filesystem`: Required to read reference files and write/overwrite configuration files in both `agents/<agent_id>/` and `pkm-oc/agents/<agent_id>/`.