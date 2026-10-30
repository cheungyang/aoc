---
name: ha_automation_authoring
description: Designs, modifies, and validates Home Assistant automations, scripts, and scenes using the propose-confirm-verify protocol.
---

## Core
This skill handles the authoring, modification, and auditing of Home Assistant automations, scripts, and scenes.
Every change goes through the `home_assistant` tool's confirmation protocol (see its description): propose, get the user's approval, apply, then verify.

### 1. Research & Design
- Designing the change is the heavy step: ask for `high` thinking in the same response as the research calls below (voice turns cap it at medium).
- Inspect existing automations with `{"action": "list_automations"}` and `{"action": "get_automation", "id": "<id>"}`.
- Design the automation structure:
  - `alias`: Clear, human-readable title.
  - `description`: Explains the trigger conditions and purpose.
  - `trigger`: Sensible event, time, state, or numeric state.
  - `condition`: Optional guards to prevent unnecessary executions.
  - `action`: Sequence of service calls touching allowed entities.
  - `mode`: Appropriate execution mode (`single`, `restart`, `queued`, `parallel`).

### 2. Propose, Then Wait
- Send the write without a token; the automation goes under `config`:
  ```json
  {
    "action": "upsert_automation",
    "config": {
      "alias": "Porch Light Sunset",
      "trigger": [{"platform": "sun", "event": "sunset", "offset": "-00:15:00"}],
      "action": [{"service": "light.turn_on", "target": {"entity_id": "light.porch"}}]
    }
  }
  ```
- **Hold Point**: Present the proposed change clearly to the user in text. Ask: *"Would you like me to apply this automation?"* Do NOT call any further write actions until the user responds.
- When the user confirms (e.g., "yes", "proceed", "looks good"), apply it as the tool's confirmation protocol describes.

### 3. Post-Apply Verification
- After a successful write, call `{"action": "check_config"}` and `{"action": "error_log"}` in one call to ensure Home Assistant parsed the configuration cleanly without errors.
- The tool rolls a write back by itself only when the stored config doesn't read back as sent. If these checks show a problem the change caused, tell the user, give the snapshot path from the write result, and offer to restore the previous version.
