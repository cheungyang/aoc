# AGENTS.md

## Morning Workflow (6:00 AM Weekdays)
When triggered for the morning digest, follow the `morning_digest` skill to generate a personalized affirmation and tactical digest. Send it to the channel and append it to today's note. Do not ask for confirmation.

## Daily Evening Workflow (4:30 PM Weekdays)
Execute this wrap-up conversation fluidly. Wait for Alva's response between each step.
**Today's briefing** provides the reads and date math (`<findings source="day_briefing">` or call `day_briefing()`). Do not recompute dates.

### Sync Check
Read today's daily note from the briefing. Check its content for recent edits indicating Alva's computer synced to the NAS. If stale, stop and ask Alva to sync, and wait. If synced, proceed.

### Highlight Wins
Run `task_query` for tasks completed today. Review today's note. Synthesize a highlight of today's accomplishments categorized by **Work** and **Personal** (daytime). 
Ask Alva: "What other wins or progress did you have today that didn't make it into the tracker?" **Wait for response**.
After he replies, use `filesystem` to `append` the finalized wins to the bottom of today's daily note (under a `#### Today's Wins` heading).

### Theme Inquiry
Ask Alva: "What is your main theme or focus for tomorrow?" (or "...for Monday?", if Friday). **Wait for response**.

### Task Alignment
1. **Gather**: Use `list_impactful_actions` to find high-priority projects and tasks (prioritizing 🔺, ⏫).
2. **Suggest**: Based on capacity and theme, suggest the **2-3 most important tasks** for the next workday.
3. Ask if these look right and if he has open loops to offload. **Wait for final confirmation**.

### Daily Summary Logging
Once agreed, follow the `write_daily_intention` skill to log the plan. It appends under `#### Next Workday's Plan`. Keep any `- [/]` items exactly as written.