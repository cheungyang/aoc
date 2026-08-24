---
name: weekly_review
description: Synthesizes the past week's daily journals and git history to collaboratively identify wins, and iteratively drafts a concise 3-point focus for the upcoming week.
---
## Overview
This skill guides the agent to synthesize the past 7 days of daily journals and `git log` commit history. It collaboratively identifies wins and iteratively drafts a 3-point focus for the next week, interacting with the user at key hold points.

## Boundaries & Guardrails
- **Human Hold Points**: You MUST halt execution at Phase 2 and Phase 3 to present drafts to the user and wait for their explicit approval before proceeding. 
- **Template Resolution**: You MUST manually resolve Obsidian Templater syntax (`<% tp... %>`) when generating the new weekly note.

## Workflow

### Phase 1: Information Gathering
1. Calculate the dates for the past 7 days.
2. Use the `filesystem` tool to `read` the fleeting notes for those dates at `pkm/vault/journals/fleeting/YYYY-MM-DD.md`.
3. Use the `git` tool's `log` action on the `pkm` directory to review commit history over the past 7 days. This will objectively show where effort was spent.

### Phase 2: Synthesize Wins & HUMAN HOLD POINT
1. Based on the daily journals and git history, compile a draft list of "Wins of the week".
2. **STOP INSTRUCTION**: Halt execution. Present the drafted wins to the user using the `<poll>` or text interactio=n. Ask the user: "What other wins should be included?"
3. **DO NOT PROCEED** until the user reviews, provides input, and gives explicit approval for the final list of wins.

### Phase 3: Project Forward & HUMAN HOLD POINT
1. Determine the active projects by executing a `project_query` tool call (using `action="search"` and filtering for active statuses like `executing` or `planning`). Ensure you exclude paused or discontinued projects.
2. Draft a short, concise 3-point list for `##🌱 Next week's focus`.
3. **STOP INSTRUCTION**: Halt execution. Present the draft focus to the user.
4. **DO NOT PROCEED** until you and the user finalize the 3 points in multiple round-trips. Wait for explicit final approval.

### Phase 4: Write Weekly Journal (Template Resolution)
1. Determine the ISO week number for the upcoming week (`YYYY-Www`).
2. Use the `filesystem` tool to `read` the template at `pkm/templates/"Weekly Review".md` (or equivalent).
3. **Template Resolution**: Parse the template and replace ALL Obsidian Templater syntax (`<% tp... %>`) with the correct static text values (dates, titles).
4. Append the finalized "Wins" and "Next week's focus" to the resolved template text.
5. Use the `filesystem` tool to `write` the fully resolved text to `pkm/vault/journals/weekly/YYYY-Www.md`.

### Phase 5: Agent-Friendly Output & Memory (IPC Format)
Finalize the execution by outputting the strict XML structure below to ensure perfect readability.
```xml
<weekly_review_response>
  <original_request>[The trigger or request to run the weekly review]</original_request>
  <triggering_agent>[Agent ID or 'User']</triggering_agent>
  <payload>
    <wins>[Final list of wins]</wins>
    <next_week_focus>[Final 3-point focus]</next_week_focus>
    <file_path>[vault/journals/weekly/YYYY-Www.md]</file_path>
  </payload>
  <errors>[Any errors or 'None']</errors>
  <learnings>[Observations about weekly productivity trends and the user's feedback during the hold points]</learnings>
</weekly_review_response>
```
**Memory Trigger**: Immediately after outputting the XML, use the `memory` skill to record the contents of the `<learnings>` tag so the system learns from this execution.

## Required Tools
- `git`: Required to `log` the `pkm` repository to review commit history.
- `project_query`: Required to retrieve active projects.
- `filesystem`: Required to `read` fleeting notes and templates, and `write` the new weekly journal in the vault.