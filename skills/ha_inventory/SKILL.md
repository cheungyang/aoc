---
name: ha_inventory
description: Inspects, summarizes, and searches smart home entities, devices, and areas across Home Assistant.
---

## Core
Read-only questions about the home (what exists, where it is, what's on) and the weekly inventory digest. The `home_assistant` tool description says which action answers what.

### Rules
- Put every lookup a question needs into one `home_assistant` call.
- Devices in your device list (`device_list`) need no lookup first: read them with `get_state` (one instruction per id), or a whole room with `search_registry` by `area` and `domain`.
- Pass `"include_disabled": true` only when troubleshooting missing or offline hardware.

### Weekly digest
In your first step, run `inventory` and call `device_list(refresh=true)`. Then report: totals, entities per area (largest first), the `(unassigned)` bucket by domain, and disabled or hidden counts. Name unassigned entities with a follow-up `search_registry` only when there are a handful. Compare with last week's totals if your memory has them, to call out what's new. If the refresh failed, say so.

Call `load_skill("ha_inventory")` for the fields `inventory` and `search_registry` return.

## Reference

### Result shapes
- `inventory`: `totals` (`entities`, `active`, `disabled`, `hidden`, `areas`), `by_area` (area to domain counts, largest area first; entities with no area under `(unassigned)`), and `by_domain`.
- `search_registry`: `total` before the cap, `returned`, `truncated`, and `entities`, each with `entity_id`, `domain`, `name`, `area`, `state`, `disabled`, `hidden`. A truncated result is a slice: narrow the filters rather than treat it as complete.
