---
name: ha_inventory
description: Inspects, summarizes, and searches smart home entities, devices, and areas across Home Assistant.
---

## Overview
This skill guides the discovery and inventory management of devices, entities, areas, and services across Home Assistant.
The home has over 1,400 registry entities and hundreds of live states. Dumping full lists wastes tokens and pollutes context; always begin broadly with summaries and filter down to specific queries.

## Workflows

### 1. Orientation & Whole-Home Overview
- **Action**: Call `{"action": "inventory"}` on `home_assistant`.
- **Outcome**: Returns a fixed-size, compact Area × Domain matrix showing device and entity counts.
- **Guidance**: Use this as the starting point whenever the user asks about the home layout, device availability, or general setup.

### 2. Targeted Search & Device Discovery
- **Action**: Call `{"action": "search_registry", "query": "...", "domain": "...", "area": "...", "include_disabled": false, "limit": 40}`.
- **Guidance**:
  - Always provide at least one filter (`query`, `domain`, or `area`).
  - By default, disabled entities are omitted; pass `"include_disabled": true` only when troubleshooting missing or offline hardware.
  - The tool caps responses and reports pre-cap totals when truncated.

### 3. Live State Assessment
- **Action**: Call `{"action": "live_context", "max_chars": 8000}`.
- **Guidance**:
  - Fetches Home Assistant's Assist snapshot showing the current state of exposed entities.
  - Useful when answering questions like "what's turned on right now?" or getting an operational snapshot of the home.
  - For a single entity, prefer `{"action": "get_state", "entity_id": "..."}` rather than fetching the whole live context.

### 4. Detailed Entity Inspection
- **Current State**: Call `{"action": "get_state", "entity_id": "<domain>.<id>"}` to inspect values, attributes, and last updated timestamps.
- **Recent History**: Call `{"action": "history", "entity_id": "<domain>.<id>", "hours": 24}` to review state transitions.
- **Events**: Call `{"action": "logbook", "hours": 6}` to view human-readable logbook entries.

## Boundaries & Best Practices
- **Never dump raw state tables**: Avoid `list_entities` without a specific domain filter.
- **Read-Only**: This skill only performs observations; it never triggers state changes or config modifications.
- **Reporting to User**: Format findings in concise markdown tables grouped by Area or Domain.
