---
name: update_permissions
description: Updates agent.json or skill.json permissions according to strict framework rules and inheritance logic.
---
## Core

Use this skill to set a new agent's or skill's baseline permissions, give an existing one new capabilities, or fix a permission error from an agent's failure context. Configs are validated on load, and an `agent.json` that fails is not loaded at all.

### Rules
1. **Implicit Allowance**: `load_skill` is always allowed. **NEVER** add it to `tools`.
2. **Inheritance & Deduplication**: Tools in an assigned skill's `skill.json` merge into the agent's grants, so don't declare a tool in `agent.json` that an assigned skill already provides.
3. **Tool Format**: `"tools": { "<toolname>": { "<path/selector>": ["<action or @bundle>"] } }`.
4. **Skill Format**: `skills` is an object keyed by skill id; each value is `{"preload": "full"}`, `{"preload": "core"}` or `{}` (on demand). The list form is rejected. Keep existing skills' settings as they are; a skill you add gets the preload the approved plan names, else `{}`.
5. **Naming**: A tool name matches `tools/<name>.py`; a skill name matches its `skills/<name>/` directory.
6. **No Guessing**: Action and bundle names are checked in code. Take them from `PERMISSION_BUNDLES` and the comments in `tools/<tool_name>.py`.
7. **Channels**: `channels` lists every channel where the agent may be included or called (`["*"]` = no restriction). A **new channel** must also be added to `channel_hosts` in `agents/main/agent.json` and to `TestLiveRoutingTable.EXPECTED` in `tests/graphs/main/test_router.py`, or the concierge won't route it.
8. **Bundles**: Prefer `@` bundles to action lists (`filesystem`: `@read`, `@write`, `@manage`; `home_assistant`: `@observe`, `@control`, `@author`, `@admin`). Unknown `@` names fail closed.
9. **Home Assistant**: **NEVER** a blanket grant (`"home_assistant": {}` or `"*": ["*"]`); always map explicit selectors to bundles.
10. **Preserve Everything Else**: You rewrite the whole file, so copy every key you aren't changing exactly as read, including `skills` settings, `thinking`, `voice`, `voice_config` and `schedules`. Never add a top-level key: unknown keys fail validation.
11. **skill.json has no `skills`**: Skills cannot inherit other skills.

### Workflow
1. Read `tools/<tool_name>.py` for every tool you grant, for its bundles or exact action names.
2. Read the target `agent.json` or `skill.json`.
3. For an `agent.json`, read the `skill.json` of every skill in its `skills` object and strip tools they already provide.
4. `overwrite` the target with the complete JSON: the file you read, with only your permission changes applied.

Call `load_skill("update_permissions")` for the bundle expansions and the full Home Assistant rules before granting `home_assistant` or composing bundles.

## Reference

### Overview
This skill safely modifies `agent.json` or `skill.json` to manage `tools` and `skills` permissions. It enforces a strict programmatic permission framework to prevent routing failures or execution errors.

### agent.json Top-Level Keys
Validation accepts only: `agent_id`, `id`, `name`, `emoji`, `description`, `model`, `provider`, `graph`, `stateless`, `channels`, `channel_hosts`, `concierge_prefixes`, `discord_token_key`, `memory_topics`, `tools`, `skills`, `schedules`, `thinking`, `voice`, `voice_config`. When updating a file, change only `tools`, `skills` and `channels` (plus the concierge's `channel_hosts`); a new `agent.json` starts from the `agent_creation` template. Schedules belong to `update_schedule`; `thinking` and `voice` come from the approved plan.

*Note on channels:* child agents do not need `channel_hosts` or `discord_token_key` defined if they rely on the concierge.

### Permission Bundles (`@` Syntax)
Named action bundles prefixed with `@` provide standard, drift-free permission sets for supported tools. Always prefer bundles over verbose action lists. Bundles expand at load time; unknown `@` names fail closed.
- **`filesystem` Bundles**:
  - `@read`: `["read", "read_image", "ls", "find", "grep"]`
  - `@write`: `["@read", "write", "overwrite", "append", "replace_block"]`
  - `@manage`: `["@write", "move", "delete", "rmdir"]` *(Note: `move` sits in `@manage`, not `@write`, because renaming files alters references across the workspace).*
  - Name the smallest bundle that covers the need. Add bare actions only to grant less than the next bundle up: `["@read", "append"]` (append-only log), `["write"]` (create-only), `["@write", "delete"]` (no `move`/`rmdir`). Never list a bundle beside its own members (`["@read", "@write"]` is `["@write"]`). Tests reject a list that spells out a whole bundle.
- **`home_assistant` Bundles**:
  - `@observe`: All read actions (`list_entities`, `get_state`, `search_registry`, `inventory`, `check_config`, `live_context`, `history`, `logbook`, `list_automations`, `get_automation`, `render_template`, `list_services`, `error_log`). Derived directly from `READ_ACTIONS`.
  - `@control`: `["call_service"]`
  - `@author`: `["upsert_automation", "upsert_script", "upsert_scene", "upsert_helper", "reload"]`
  - `@admin`: `["@author", "delete_automation", "delete_script", "delete_scene"]`

### Home Assistant Security & Facade Rules
- **Blanket `{}` Grants Are Refused**: **NEVER** write `"home_assistant": {}` or `"*": ["*"]`. `home_assistant` fails closed and explicitly refuses blanket grants for write actions. You must always provide explicit selector-to-actions/bundles mappings.
- **Selector Scoping**:
  - Use `"*": ["@observe"]` for whole-home observation.
  - Use domain globs (`light.*`, `switch.*`, `fan.*`, `climate.*`, etc.) for control: `["@control"]`.
  - Use authoring domains (`automation.*`, `script.*`, `scene.*`) for automation management: `["@control", "@author"]`.
- **`@control` Does NOT Include `@observe`**: They are granted against different selectors (`*` for observe vs domain globs for control). Folding observe into control would silently widen narrow domain grants.
- **Programmatic Deny-List**: Domains `lock`, `alarm_control_panel`, `cover`, `garage_door`, `valve`, and `water_heater` are blocked in code. Never attempt to grant them.
- **Climate Safe Band**: 12.0°C – 28.0°C is enforced in code.

### Required Tools
- `filesystem`: Required to read python files (to validate actions/bundles), read `skill.json`/`agent.json` files (for inheritance logic), and overwrite the final target JSON file.