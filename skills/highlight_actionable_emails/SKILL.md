---
name: highlight_actionable_emails
description: Securely interface with the gog tool to fetch recent emails and return a clean, token-efficient summary of actionable items.
---
## Goal
Process incoming Gmail and extract a token-efficient summary of highly relevant, actionable tasks or urgent communications.

## Workflow
1. **Search**: Run the `gog` tool with `gmail search 'newer_than:3d to:me' --json`.
2. **Parse Data**: Extract the Sender, Subject, and Snippet of each email.
3. **Apply Heuristics**:
   - Tag `[CRITICAL]` for emails from the critical senders your human context lists (banks, government offices).
   - Tag `[VIP]` for the family senders your human context lists.
   - Tag `[ACTIONABLE]` for other emails representing real-world tasks, requests, or deadlines.
4. **Filter & Omit**: Completely omit newsletters, promotions, marketing materials, and spam.
5. **Return**: A clean, prioritized list (using the tags above) with a short explanation of why each email was flagged, so it can be triaged at a glance.
