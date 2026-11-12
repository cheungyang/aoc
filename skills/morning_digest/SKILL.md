---
name: morning_digest
description: Generates a personalized daily affirmation and tactical digest, appending it to today's daily note.
---
Generate the morning affirmation and tactical digest, output it to the user, and append it to today's daily note.
1. In one step, call `day_briefing()` (to get today's note path), `task_query` (for tasks due today or overdue), and use the `highlight_actionable_emails` skill to get the inbox triage.
2. Synthesize the digest:
   - **Affirmation**: A brief, grounded paragraph leveraging Alva's human context (goals, family, career at Google) to provide motivation and perspective.
   - **Today's Intention**: Read yesterday's note from the briefing. If it has a `#### Next Workday's Plan` section, extract the most impactful task and feature it here. If the section is missing or empty, explicitly call this out so Alva can add a focus for the day to the digest.
   - **Tactical Triage**: A list of the urgent/overdue tasks and VIP/actionable emails. Format these strictly as plain text bullets (e.g., `* Task name`), NEVER as actionable checkboxes (`- [ ]`), so they don't bloat the task tracker.
3. Use `filesystem` to `append` this entire digest to the bottom of today's daily note (path provided by `day_briefing`). 
4. Send the exact same digest as a read-only message to the user in the channel. Do not ask for confirmation or prompt for next steps.
