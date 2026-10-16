---
name: write_daily_intention
description: Generates the daily journal, resolves templates, and appends the agreed intentions.
---
Log the agreed plan in today's daily note, `pkm/vault/journals/fleeting/YYYY-MM-DD.md` (always pass this exact relative path to `filesystem`).
1. `read` today's note. If it does not exist, `read` the template `pkm/templates/Fleeting Pages.md`, replace ALL Obsidian Templater syntax (`<% tp... %>`) with static values (e.g. `<% tp.date.now("w")-1 %>` becomes last week's ISO week number; date and title syntax becomes today's date), and `write` the result to today's path.
2. `append` to the very bottom of the note: empty lines (so the markdown renders), the header `#### Agent's recommendation`, then the final morning reflection, energy level, intentions, and agreed tasks, keeping any `- [/]` items exactly as the user wrote them.
3. Confirm the saved path in one line. Log observations on the user's priorities or mood in your `<system_memory_log>`.