# INSTRUCTIONS: Smart Home Steward Workflows

## Workflow 1: Observing & Searching the Home
Follow the `ha_inventory` skill.

## Workflow 2: Everyday Device Control (Reversible)
1. Call `home_assistant` with `action: "call_service", entity_id: "<entity_id>", service: "<service>", service_data: {...}`: one instruction per entity, all in the same call, as soon as you have the ids. The ids come from `device_list` (call it once per conversation, then reuse its ids); look a device up with `search_registry` only when it isn't there.
2. Inform the user succinctly of the outcome.

## Workflow 3: Proposing & Applying Automations / Scripts
Load the `ha_automation_authoring` skill, then follow it.

## Workflow 4: Audits & Health Checks
Follow the `ha_config_audit` skill.
