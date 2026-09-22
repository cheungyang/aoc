---
name: ha_automation_authoring
description: Designs, modifies, and validates Home Assistant automations, scripts, and scenes using the propose-confirm-verify protocol.
---

## Overview
This skill handles the authoring, modification, and auditing of Home Assistant automations, scripts, and scenes.
All mutations to stored Home Assistant configuration follow the strict **propose → confirm → apply → verify** pipeline with automatic before-image rollback protection.

## Safety & Autonomy Policies
1. **Programmatic Deny-List**:
   - The following domains are **refused in code** and must NEVER be targeted by any automation or service call:
     `lock`, `alarm_control_panel`, `cover`, `garage_door`, `valve`, `water_heater`.
   - Climate actions must stay within the safe band of 12.0°C – 28.0°C.
2. **Mandatory Confirmation**:
   - Every config modification (`upsert_automation`, `upsert_script`, `upsert_scene`, `delete_*`, `reload`) requires human approval via a confirm token.
   - Opaque service executions (`script.turn_on`, `automation.trigger`) also require confirmation.

## Workflows

### 1. Research & Design
- Inspect existing automations with `{"action": "list_automations"}` and `{"action": "get_automation", "id": "<id>"}`.
- Verify referenced entities exist using `{"action": "search_registry", ...}` or `{"action": "get_state", ...}`.
- Design the automation structure:
  - `alias`: Clear, human-readable title.
  - `description`: Explains the trigger conditions and purpose.
  - `trigger`: Sensible event, time, state, or numeric state.
  - `condition`: Optional guards to prevent unnecessary executions.
  - `action`: Sequence of service calls touching allowed entities.
  - `mode`: Appropriate execution mode (`single`, `restart`, `queued`, `parallel`).

### 2. The Propose → Confirm Protocol
Always execute config mutations in two distinct steps:

#### Step A: Proposal (Dry-Run)
- Call the mutation action without a token:
  ```json
  {
    "action": "upsert_automation",
    "automation": {
      "alias": "Porch Light Sunset",
      "trigger": [{"platform": "sun", "event": "sunset", "offset": "-00:15:00"}],
      "action": [{"service": "light.turn_on", "target": {"entity_id": "light.porch"}}]
    }
  }
  ```
- The tool will return:
  ```xml
  <confirmation_required action="upsert_automation" target="automation.porch_light_sunset">
    <diff>...</diff>
    <confirm_token>sha256:...</confirm_token>
    <expires_in>300s</expires_in>
  </confirmation_required>
  ```
- **Hold Point**: Present the proposed changes and diff clearly to the user in text. Ask: *"Would you like me to apply this automation?"* Do NOT call any further write actions until the user responds.

#### Step B: Application (Upon Human Approval)
- When the user confirms (e.g., "yes", "proceed", "looks good"):
  Re-issue the instruction including `"confirm_token"`:
  ```json
  {
    "action": "upsert_automation",
    "confirm_token": "<token_from_step_a>",
    "automation": { ... }
  }
  ```
- If the token expired (300s TTL) or was invalidated by payload modification, re-run Step A to generate a fresh token.

### 3. Post-Apply Verification
- After a successful write, call `{"action": "check_config"}` and `{"action": "error_log"}` to ensure Home Assistant parsed the configuration cleanly without errors.
- If verification fails, the underlying engine automatically rolls back to the before-image snapshot saved in `pkm/agents/home-steward/ha_snapshots/`.
