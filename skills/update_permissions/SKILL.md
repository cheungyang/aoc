---
name: update_permissions
description: Updates agent.json or skill.json permissions according to strict framework rules and inheritance logic.
---
## Overview
This skill safely modifies `agent.json` or `skill.json` to manage `tools` and `skills` permissions. It enforces a strict programmatic permission framework to prevent routing failures or execution errors.

## Triggers
Use this skill when:
- Creating a new agent or skill and establishing its baseline permissions.
- Updating an existing agent or skill to give it new capabilities.
- Debugging permission errors based on an agent's failure context.

## The 7 Immutable Permission Rules
1. **Implicit Allowance**: `load_skill` is inherently allowed in the system. **NEVER** add `load_skill` to the `tools` permission list in any file.
2. **Inheritance & Deduplication**: Tools permitted inside a loaded skill (`skills/<skill_name>/skill.json`) merge seamlessly with `agent.json`. Therefore, you do NOT need to declare a tool in `agent.json` if a loaded skill already provides it.
3. **Tool Format Enforcement**: 
   The `tools` block MUST strictly be a dictionary mapping tools to paths and lists of actions:
   `"tools": { "<toolname>": { "<path and its subpaths to allow>": ["<list of actions allowed for this path>"] } }`
4. **Skill Format Enforcement**: 
   The `skills` block MUST strictly be an array of skill IDs:
   `"skills": ["<list of skill allowed>"]`
5. **Tool Naming**: The name of a tool MUST exactly match its filename (without `.py`) located in the `tools/` directory.
6. **Skill Naming**: The name of a skill MUST exactly match its directory/filename located in the `skills/` directory.
7. **Action Name Validation (No Guessing)**: You MUST NOT guess or hallucinate action names. Action names are programmatically checked. You MUST find them by reading the comments inside the individual `tools/<tool_name>.py` file.
8. **Channel Restrictions Enforcement**: The `channels` block in `agent.json` MUST strictly be an array of channel names where the agent is allowed to be included or called (e.g., `"channels": ["day-planning", "weekly-planning"]`). Use `"*"` inside the array to indicate the agent has no channel restrictions (e.g., `"channels": ["*"]`).

## Execution Workflow

### Step 1: Discover Available Actions (Mandatory)
Before writing any permissions, you MUST use the `filesystem` tool to `read` the corresponding `tools/<tool_name>.py` file. 
- Look at the comments inside the file to extract the exact strings for allowed actions.

### Step 2: Read Existing Configurations
Use the `filesystem` tool to `read` the target `agent.json` or `skill.json` to get the current state.

### Step 3: Check Inheritance (If modifying `agent.json`)
If you are modifying an agent's `agent.json`, you MUST read the `skill.json` of every skill listed in its `skills` array. 
- Compare the inherited tools against the tools the agent needs. 
- Strip out any explicitly defined tools in `agent.json` that are already covered by an inherited skill.

### Step 4: Validate Skill vs Agent Differences
- If you are modifying an `agent.json`, it may contain both `skills` and `tools` objects.
- If you are modifying a `skill.json`, it **MUST NOT** contain a `skills` object. Skills cannot inherit other skills.

### Step 5: Overwrite File
Generate the final, fully-compliant JSON payload and use the `filesystem` tool's `overwrite` action to save the target `agent.json` or `skill.json`.

## Required Tools
- `filesystem`: Required to read python files (to validate actions), read `skill.json`/`agent.json` files (for inheritance logic), and overwrite the final target JSON file.