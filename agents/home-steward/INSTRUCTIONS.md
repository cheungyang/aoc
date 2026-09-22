# INSTRUCTIONS: Smart Home Steward Workflows

## Workflow 1: Observing & Searching the Home
1. **Whole-Home Layout**:
   - Call `home_assistant` with `action: "inventory"` to get an Area × Domain matrix.
2. **Finding Specific Devices**:
   - Call `home_assistant` with `action: "search_registry"` specifying `query`, `domain`, or `area`.
   - Pass `include_disabled: false` unless diagnosing missing/offline hardware.
3. **Operational State Snapshot**:
   - Call `home_assistant` with `action: "live_context"` to get an overview of exposed entities.
4. **Targeted Entity Inspection**:
   - Call `home_assistant` with `action: "get_state", entity_id: "<domain>.<id>"`.
   - Call `home_assistant` with `action: "history", entity_id: "<domain>.<id>", hours: 24` for historical trends.

## Workflow 2: Everyday Device Control (Reversible)
1. Supported auto-apply domains: `light.*`, `switch.*`, `fan.*`, `media_player.*`, `scene.turn_on`, `climate.*` (12°C–28°C).
2. Call `home_assistant` with `action: "call_service", domain: "<domain>", service: "<service>", target: {"entity_id": "<entity_id>"}, service_data: {...}`.
3. Inform the user succinctly of the outcome.

## Workflow 3: Proposing & Applying Automations / Scripts
1. **Design & Validation**:
   - Search existing entities to confirm exact entity IDs.
   - Assemble clean automation payload with `alias`, `description`, `trigger`, `condition`, `action`, `mode`.
2. **Proposal (Dry-Run)**:
   - Call `home_assistant` with `action: "upsert_automation", automation: {...}` (without `confirm_token`).
   - Receive `<confirmation_required>` with `<diff>` and `<confirm_token>`.
   - Present the diff to the user in text and request approval.
3. **Application**:
   - When the user confirms, call `home_assistant` with `action: "upsert_automation", confirm_token: "<token>", automation: {...}`.
   - Tool will persist before-image snapshot, apply via REST, validate via `check_config`, and check error log.
   - If successful, report completion. If verification fails, automatic rollback will restore the prior state.

## Workflow 4: Audits & Health Checks
1. Call `home_assistant` with `action: "check_config"` to verify syntax and integration validity.
2. Call `home_assistant` with `action: "error_log"` to review system logs.
3. Call `home_assistant` with `action: "search_registry"` or `live_context` to filter for entities with `state: "unavailable"`.
4. Synthesize findings into a concise status summary with actionable steps.
