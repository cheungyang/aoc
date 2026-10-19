---
name: weekly_review
description: Synthesizes the past week's daily journals and git history to collaboratively identify wins, and iteratively drafts a concise 3-point focus for the upcoming week.
---
## Overview
This skill guides the agent to synthesize the past 7 days of daily journals and `git log` commit history. It collaboratively identifies wins and iteratively drafts a 3-point focus for the next week, interacting with the user at key hold points.

## Boundaries & Guardrails
- **Human Hold Points**: You MUST halt execution at Phase 2 and Phase 3 to present drafts to the user and wait for their explicit approval before proceeding. 
- **Weekly Note**: Write it only with `write_weekly_review`. Never name the note or resolve Templater syntax (`<% tp... %>`) yourself.

## Workflow

### Phase 1: Information Gathering
1. Calculate the dates for the past 7 days.
2. Use the `filesystem` tool to `read` the fleeting notes for those dates at `pkm/vault/journals/fleeting/YYYY-MM-DD.md`.
3. Use the `git` tool's `log` action on the `pkm` directory to review commit history over the past 7 days. This will objectively show where effort was spent.

### Phase 2: Synthesize Wins & HUMAN HOLD POINT
1. Based on the daily journals and git history, compile a draft list of "Wins of the week", grouped under the weekly note's headings: `🏆 Works wins`, `🏆 Family wins`, `🏆 Personal development wins`.
2. **STOP INSTRUCTION**: Halt execution. Present the drafted wins to the user using the `<poll>` or text interaction. Ask the user: "What other wins should be included?"
3. **DO NOT PROCEED** until the user reviews, provides input, and gives explicit approval for the final list of wins.

### Phase 3: Project Forward & HUMAN HOLD POINT
1. Determine the active projects by executing a `project_query` tool call (using `action="search"` and filtering for active statuses like `executing` or `planning`). Ensure you exclude paused or discontinued projects.
2. Draft a short, concise 3-point list for `🌱 Next week's focus`.
3. **STOP INSTRUCTION**: Halt execution. Present the draft focus to the user.
4. **DO NOT PROCEED** until you and the user finalize the 3 points in multiple round-trips. Wait for explicit final approval.

### Phase 4: Write Weekly Journal
Call `write_weekly_review` once, with the approved wins under their 🏆 headings and the 3 points under `🌱 Next week's focus`, as approved. If it says a section already has text, show that text to the user and call again as they decide.

### Phase 5: Recap & Memory
Reply in plain Markdown, never XML (your reply is shown in Discord): the final wins, next week's 3-point focus, the journal's path (as `write_weekly_review` returns it), and any errors.
**Memory Trigger**: End with a `<system_memory_log>` block recording weekly productivity trends and the user's feedback at the hold points.

## Required Tools
- `git`: Required to `log` the `pkm` repository to review commit history.
- `project_query`: Required to retrieve active projects.
- `filesystem`: Required to `read` fleeting notes.
- `write_weekly_review`: Required to write the weekly journal into the week's note.