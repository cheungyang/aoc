---
name: web_browsing
description: Skill for utilizing visual AI navigation to achieve specific web goals, ending with a Markdown report.
---
## Overview
This skill empowers the agent to automate repetitive visual web interactions that require user sessions (like logged-in states or anti-bot bypass) using the user's active Chrome instance. It ends with a short Markdown report for the user.

## Boundaries & Guardrails
- **The "Final Click" Rule**: You MUST NEVER execute destructive actions or finalize financial transactions (e.g., clicking "Pay Now", "Delete Account", "Confirm Booking"). You must stage the action up to the final confirmation page and notify the user to take manual control.
- **Session Disruption**: Do NOT close the user's active tabs or disrupt their primary workspace unless explicitly requested by the user.
- **Formatting**: Reply in plain Markdown, never XML: your reply is shown in Discord, even when the Concierge relays the request.

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

### Step 3: Report & Memory
Read the output of the `browser` tool and reply in plain Markdown:
- If the "Final Click" Rule stopped you, say so first and tell the user to take manual control.
- The URL the browser ended on, any data you were asked to extract, and a brief summary of the path taken.
- Any errors: DOM resolution failures, timeouts, captcha blocks.

**Memory Trigger**: End with a `<system_memory_log>` block recording what you learned about the site's structure (e.g., "Site X requires scrolling before button Y appears"), to speed up future navigations.

## Required Tools
- `browser`: Required to connect to and control the user's active Chrome session via port 9222.