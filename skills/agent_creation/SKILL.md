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
      - pkm/agents/<agent_id>/CONTEXT.md
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