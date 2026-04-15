---
name: web_browsing
description: Skill for utilizing visual AI navigation to achieve specific web goals, outputting IPC XML.
---
## Overview
This skill empowers the agent to automate repetitive visual web interactions that require user sessions (like logged-in states or anti-bot bypass) using the user's active Chrome instance. It concludes by outputting a standard IPC XML block.

## Boundaries & Guardrails
- **The "Final Click" Rule**: You MUST NEVER execute destructive actions or finalize financial transactions (e.g., clicking "Pay Now", "Delete Account", "Confirm Booking"). You must stage the action up to the final confirmation page and notify the user to take manual control.
- **Session Disruption**: Do NOT close the user's active tabs or disrupt their primary workspace unless explicitly requested by the user.
- **Formatting**: The final output MUST strictly adhere to the requested IPC XML structure.

## Prerequisite: Remote Debugging Setup
Before executing this skill, generic isolated Chrome listeners must be allocated explicitly into active host memory:
1. **Launch Isolated Session**:
   ```bash
   /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome_debug
   ```
2. **Verify Allocations**: Ensure explicit Process IDs are live before starting multi-agent bounds routines:
   ```bash
   lsof -i :9222
   ```

## Workflow

### Step 1: Formulate the Goal
- Identify the specific objective the user wants to achieve.
- Formulate a concise but highly specific visual goal string.
- *Example String:* "Go to Resy.com. Search for Balthazar NYC. Find a table for 2 guests on Friday around 7 PM. Click the time slot to proceed to the details input page."

### Step 2: Execute Automation
- Use the `browser` tool.
- Pass the formulated visual goal string as the `goal` parameter.
- Pass `9222` as the `port` parameter (this is the hardcoded default unless the user specifies otherwise).

### Step 3: Agent-Friendly Output & Memory (IPC Format)
Read the output of the `browser` tool and finalize the execution using the strict XML structure below. This format ensures perfect readability for routing agents and clearly logs the boundary hand-offs.
```xml
<web_browsing_response>
  <original_request>[The specific navigation or extraction goal]</original_request>
  <triggering_agent>[Agent ID or 'User']</triggering_agent>
  <payload>
    <final_url>[The URL the browser ended up on]</final_url>
    <extracted_data>[Any specific information requested to be extracted from the DOM]</extracted_data>
    <actions_taken>[Brief summary of the automation path taken]</actions_taken>
    <handoff_required>[True/False based on the "Final Click" Rule]</handoff_required>
  </payload>
  <errors>[Any DOM resolution errors, timeout issues, captcha blocks, or 'None']</errors>
  <learnings>[Insights into site structure (e.g., 'Site X requires scrolling before button Y appears') to optimize future navigations]</learnings>
</web_browsing_response>
```
**User Notification & Memory Trigger**: 
- If `handoff_required` is True, explicitly notify the user to take manual control.
- Immediately after outputting the XML, use the `memory` skill to record the contents of the `<learnings>` tag so the system learns from this browsing session.

## Required Tools
- `browser`: Required to connect to and control the user's active Chrome session via port 9222.