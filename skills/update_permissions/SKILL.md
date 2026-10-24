---
name: update_permissions
description: Updates agent.json or skill.json permissions according to strict framework rules and inheritance logic, including deny entries and the read grants an agent's tools need.
---
## Core

Use it to set a new agent's or skill's permissions, extend them, or fix a permission error from an agent's failure context. Configs are validated on load; an `agent.json` that fails is not loaded.

### Rules
1. **Implicit Allowance**: `load_skill` is always allowed. **NEVER** add it to `tools`.
2. **Inheritance**: tools in an assigned skill's `skill.json` merge into the agent's grants, so don't repeat them in `agent.json`. A `skill.json` has no `skills`.
3. **Format**: `"tools": { "<tool>": { "<path or selector>": ["<action or @bundle>"] } }`. `skills` is an object keyed by skill id: `{"preload": "full"}`, `{"preload": "core"}` or `{}` (on demand). Keep existing skills' settings; a skill you add gets the preload the approved plan names, else `{}`.
4. **Naming**: a tool is `tools/<name>.py` (shared) or `agents/<agent_id>/tools/<name>.py` (that agent's only). Take action and bundle names from the tool's module (`PERMISSION_BUNDLES`, its comments); never guess.
5. **Bundles**: prefer them to action lists (`filesystem`: `@read`, `@write`, `@manage`; `home_assistant`: `@observe`, `@control`, `@author`, `@admin`). Unknown `@` names fail closed. Name each once, the smallest that covers the need: `@write` holds `@read`, so `["@read", "@write"]` is `["@write"]`. **NEVER** a blanket `home_assistant` grant; map explicit selectors to bundles.
6. **`<agent_id>`** in a grant key is the agent's own id, in `agent.json` and `skill.json` alike: `"pkm/agents/<agent_id>/"`.
7. **Deny entries**: a key starting with `!` lists actions refused under its path, and a refusal beats any grant. `*` matches one folder name, only in `!` keys; grant keys are plain paths. `@change` (every action that modifies) is meant for deny entries. agent-designer keeps its five (Reference: Deny Entries).
8. **Read grants**: an agent may read every source its tools read (each tool docstring's `Reads:` line), so a broken tool slows it down instead of blinding it. A file a tool owns (a ledger, a workout history) gets `@read` only: writes go through the tool.
9. **RULES.md**: every agent can already write its own `pkm/agents/<agent_id>/` folder, so RULES.md needs no grant.
10. **Channels**: `channels` lists every channel the agent may be included or called in (`["*"]`: any). A **new channel** also goes in `channel_hosts` of `agents/main/agent.json` and in `TestLiveRoutingTable.EXPECTED` (`tests/core/runtime/foundation/test_routing.py`), or the concierge won't route it. It is the only such table; an entry is `None` once agents share the channel, and `agent_lint` names the line to write.
11. **Preserve Everything Else**: you rewrite the whole file, so copy every key you aren't changing exactly as read, including `skills` settings, `thinking`, `memory_topics`, `voice`, `voice_config` and `schedules`. Never add a top-level key: unknown keys fail validation.

### Workflow
1. Read in one step: the target `agent.json` or `skill.json`, the module of every tool you grant (rule 4), and, for an `agent.json`, each of its skills' `skill.json` (rule 2).
2. `overwrite` the target with the complete JSON: the file you read, with only your permission changes applied.
3. Run `agent_lint` on the agent and fix what it finds before reporting done.

Call `load_skill("update_permissions")` for the bundle expansions, deny examples and the full Home Assistant rules before granting `home_assistant`, writing a deny entry or composing bundles.

## Reference

### Overview
This skill safely modifies `agent.json` or `skill.json` to manage `tools` and `skills` permissions. It enforces a strict programmatic permission framework to prevent routing failures or execution errors.

### agent.json Top-Level Keys
Validation accepts only: `agent_id`, `id`, `name`, `emoji`, `description`, `model`, `provider`, `graph`, `stateless`, `channels`, `channel_hosts`, `concierge_prefixes`, `discord_token_key`, `memory_topics`, `tools`, `skills`, `schedules`, `thinking`, `voice`, `voice_config`. When updating a file, change only `tools`, `skills` and `channels` (plus the concierge's `channel_hosts`); a new `agent.json` starts from the `agent_creation` template. Schedules belong to `update_schedule`; `thinking` and `voice` come from the approved plan.

*Note on channels:* only the concierge (`main`) has `channel_hosts` and `discord_token_key`: it runs the system's only Discord bot and hosts every channel, and hands each channel to its agent (rule 10). No other agent defines them.

### Permission Bundles (`@` Syntax)
Named action bundles prefixed with `@` provide standard, drift-free permission sets for supported tools. Always prefer bundles over verbose action lists. Bundles expand at load time; unknown `@` names fail closed.
- **`filesystem` Bundles**:
  - `@read`: `["read", "read_image", "ls", "find", "grep"]`
  - `@write`: `["@read", "write", "overwrite", "append", "replace_block"]`
  - `@manage`: `["@write", "move", "delete", "rmdir"]` *(Note: `move` sits in `@manage`, not `@write`, because renaming files alters references across the workspace).*
  - `@change`: `["write", "overwrite", "append", "replace_block", "move", "delete", "rmdir"]`, every action that modifies and none that reads. For deny entries; as a grant it would give write without read.
  - Name the smallest bundle that covers the need. Add bare actions only to grant less than the next bundle up: `["@read", "append"]` (append-only log), `["write"]` (create-only), `["@write", "delete"]` (no `move`/`rmdir`). Never list a bundle beside its own members (`["@read", "@write"]` is `["@write"]`). Tests reject a list that spells out a whole bundle.
- **`home_assistant` Bundles**:
  - `@observe`: All read actions (`list_entities`, `get_state`, `search_registry`, `inventory`, `check_config`, `live_context`, `history`, `logbook`, `list_automations`, `get_automation`, `render_template`, `list_services`, `error_log`). Derived directly from `READ_ACTIONS`.
  - `@control`: `["call_service"]`
  - `@author`: `["upsert_automation", "upsert_script", "upsert_scene", "upsert_helper", "reload"]`
  - `@admin`: `["@author", "delete_automation", "delete_script", "delete_scene"]`

### Deny Entries
agent-designer manages agent definitions but never agent code or WORKFLOW.md; both change through a reviewed PR:
```json
"filesystem": {
  "agents/": ["@manage"],
  "!agents/*/tools/": ["@change"],
  "!agents/*/preconditions/": ["@change"],
  "!agents/*/lib/": ["@change"],
  "!agents/*/WORKFLOW.md": ["@change"],
  "!tests/agents/": ["@change"]
}
```
- Reads stay allowed under a deny entry: agent-designer reads code to write a spec.
- A move is checked at its destination too, so a definition file can't be moved into `tools/`.
- Deny entries apply to path grants (`filesystem`, `bash`). A tool that matches its own selectors (Home Assistant) ignores them.
- `agent_lint` reports a malformed one (`malformed_deny`): no path after the `!`, an action the tool doesn't have, a bundle it doesn't declare, or a folder that doesn't exist.

### Grants for an Agent's Tools
- Each source a tool reads is on its docstring's `Reads:` line; the agent gets `@read` on it, from `agent.json` or the skill that calls the tool. `agent_lint` reports a missing one (`read_not_granted`).
- A file the user edits (criteria, staples, preferences) gets `@read` plus the single action its skill uses (`replace_block`, `append`), and the skill changes one confirmed line at a time.

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
- `filesystem`: Required to read tool modules in `tools/` and `agents/<agent_id>/tools/` (to validate actions/bundles), read `skill.json`/`agent.json` files (for inheritance logic), and overwrite the final target JSON file.
- `agent_lint`: to check the agent's grants once the file is written.