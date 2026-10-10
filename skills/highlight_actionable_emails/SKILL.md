---
name: highlight_actionable_emails
description: Securely interface with the gog tool to fetch recent emails and return a clean, token-efficient summary of actionable items.
---
## Goal
Process incoming Gmail and extract a token-efficient summary of highly relevant, actionable tasks or urgent communications.

## Workflow
1. **Search**: Execute the `gog` tool with the command: `gmail search 'newer_than:3d to:me' --json`.
2. **Parse Data**: Process the returned JSON to extract the Sender, Subject, and Snippet for each email.
3. **Apply Heuristics**:
   - Evaluate the extracted data against the following rules:
     - Tag `[CRITICAL]` for emails from HSBC or the DMV.
     - Tag `[VIP]` for the family whitelist (e.g., `chanyash@gmail.com`).
     - Tag `[ACTIONABLE]` for other emails representing real-world tasks, requests, or deadlines.
4. **Filter & Omit**: Completely omit newsletters, promotions, marketing materials, and spam. Do not include them in the summary.
5. **Return**: Provide a clean, prioritized list (using the tags above) with a short explanation of why the email was flagged, so the calling agent can easily triage it.

## Required Tools
- `gog`: Required to search and fetch recent emails from Gmail.
