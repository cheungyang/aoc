---
name: ha_config_audit
description: Audits Home Assistant health, error logs, unavailable entities, and automation integrity.
---

## Core
A structured, read-only health check of Home Assistant, on demand or as the daily scheduled audit.

### Checks: one `home_assistant` call with all of these
1. `check_config`: any result other than `valid` is an immediate attention item.
2. `error_log`: look for repeated exceptions, connection timeouts to external APIs, and template rendering failures. Separate benign warnings from critical failures (failing integrations, database corruption).
3. Unavailable entities: `render_template` with `{{ states | selectattr('state', 'in', ['unavailable', 'unknown']) | map(attribute='entity_id') | list }}`. Check whether whole devices or integrations are offline (battery-depleted Zigbee/Z-Wave sensors, disconnected Wi-Fi plugs).
4. `list_automations`: flag automations that are off or disabled. For any that look broken, cross-check the entity ids they reference against known entities to catch typos or deleted devices.
5. `get_config` and `inventory`, for the version and entity totals.

### Report
- **System Health**: version, config check status, total entity count.
- **Offline / Unavailable Devices**: grouped by area or domain.
- **Notable Errors**: high-signal items from the error log.
- **Action Items**: concrete steps the user or Butler can take to resolve anomalies.
- No issues: one line, *"Home Assistant audit clean: config is valid, all devices healthy, no recent error logs."*

Call `load_skill("ha_config_audit")` for how to dig into a finding: an automation's references, or when an entity went offline.

## Reference

### Digging into findings
- **Automation references**: `get_automation` with the numeric `id` from `list_automations`, then look the referenced entity ids up with `search_registry` or `get_state` to catch typos or deleted devices.
- **When an entity went offline**: `history` on the entity, or `logbook` for the surrounding events.
