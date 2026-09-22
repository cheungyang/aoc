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

## The 11 Immutable Permission & Configuration Rules
1. **Implicit Allowance**: `load_skill` is inherently allowed in the system. **NEVER** add `load_skill` to the `tools` permission list in any file.
2. **Inheritance & Deduplication**: Tools permitted inside a loaded skill (`skills/<skill_name>/skill.json`) merge seamlessly with `agent.json`. Therefore, you do NOT need to declare a tool in `agent.json` if a loaded skill already provides it.
3. **Tool Format Enforcement**: 
   The `tools` block MUST strictly be a dictionary mapping tools to paths/selectors and lists of actions or bundles:
   `"tools": { "<toolname>": { "<path/selector>": ["<action or @bundle>"] } }`
4. **Skill Format Enforcement**: 
   The `skills` block MUST strictly be an array of skill IDs:
   `"skills": ["<list of skill allowed>"]`
5. **Tool Naming**: The name of a tool MUST exactly match its filename (without `.py`) located in the `tools/` directory.
6. **Skill Naming**: The name of a skill MUST exactly match its directory/filename located in the `skills/` directory.
7. **Action & Bundle Name Validation (No Guessing)**: You MUST NOT guess or hallucinate action or bundle names. Action names and bundles are programmatically checked. You MUST find them by reading `PERMISSION_BUNDLES` and comments inside the individual `tools/<tool_name>.py` file.
8. **Channel Restrictions Enforcement**: The `channels` block in an agent's `agent.json` MUST strictly be an array of channel names where the agent is allowed to be included or called. When setting up an agent to communicate in a new channel, you MUST ensure that channel is explicitly listed in its `channels` array. Use `"*"` inside the array to indicate the agent has no channel restrictions (e.g., `"channels": ["*"]`).
9. **Concierge Channel Hosting**: If an agent configuration update introduces a **new channel**, you MUST also ensure that new channel is added to the `channel_hosts` array in the `concierge` agent's configuration (`agents/main/agent.json`) and mapped in `TestLiveRoutingTable.EXPECTED` in `tests/graphs/main/test_router.py`. Without this, the concierge will not listen to or route messages from that channel. *(Note: Child agents do not need `channel_hosts` or `discord_token_key` defined if they rely on the concierge).*
10. **Permission Bundles (`@` Syntax)**:
    Named action bundles prefixed with `@` provide standard, drift-free permission sets for supported tools. Always prefer bundles over verbose action lists. Bundles expand at load time; unknown `@` names fail closed.
    - **`filesystem` Bundles**:
      - `@read`: `["read", "read_image", "ls", "find", "grep"]`
      - `@write`: `["@read", "write", "overwrite", "append", "replace_block"]`
      - `@manage`: `["@write", "move", "delete", "rmdir"]` *(Note: `move` sits in `@manage`, not `@write`, because renaming files alters references across the workspace).*
    - **`home_assistant` Bundles**:
      - `@observe`: All read actions (`list_entities`, `get_state`, `search_registry`, `inventory`, `check_config`, `live_context`, `history`, `logbook`, `list_automations`, `get_automation`, `render_template`, `list_services`, `error_log`). Derived directly from `READ_ACTIONS`.
      - `@control`: `["call_service"]`
      - `@author`: `["upsert_automation", "upsert_script", "upsert_scene", "upsert_helper", "reload"]`
      - `@admin`: `["@author", "delete_automation", "delete_script", "delete_scene"]`
11. **Home Assistant Security & Facade Rules**:
    - **Blanket `{}` Grants Are Refused**: **NEVER** write `"home_assistant": {}` or `"*": ["*"]`. `home_assistant` fails closed and explicitly refuses blanket grants for write actions. You must always provide explicit selector-to-actions/bundles mappings.
    - **Selector Scoping**:
      - Use `"*": ["@observe"]` for whole-home observation.
      - Use domain globs (`light.*`, `switch.*`, `fan.*`, `climate.*`, etc.) for control: `["@control"]`.
      - Use authoring domains (`automation.*`, `script.*`, `scene.*`) for automation management: `["@control", "@author"]`.
    - **`@control` Does NOT Include `@observe`**: They are granted against different selectors (`*` for observe vs domain globs for control). Folding observe into control would silently widen narrow domain grants.
    - **Programmatic Deny-List**: Domains `lock`, `alarm_control_panel`, `cover`, `garage_door`, `valve`, and `water_heater` are blocked in code. Never attempt to grant them.
    - **Climate Safe Band**: 12.0°C – 28.0°C is enforced in code.

## Execution Workflow

### Step 1: Discover Available Actions & Bundles (Mandatory)
Before writing any permissions, use the `filesystem` tool to `read` the corresponding `tools/<tool_name>.py` file:
- Check for a top-level `PERMISSION_BUNDLES` dictionary:
  - If available, use the appropriate `@bundle` alias (e.g. `@read`, `@write`, `@observe`, `@control`, `@author`).
  - If a bundle is not defined or granular sub-grants are required, check the comments and docstrings in the tool file for exact action names.

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
- `filesystem`: Required to read python files (to validate actions/bundles), read `skill.json`/`agent.json` files (for inheritance logic), and overwrite the final target JSON file.