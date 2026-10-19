---
name: write_daily_intention
description: Creates today's daily note when it is missing, and appends the agreed intentions.
---
Log the agreed plan in today's daily note, at the path in the "Today's note" line of today's briefing (`pkm/vault/journals/fleeting/YYYY-MM-DD.md`; always pass that exact relative path to `filesystem`). If today's briefing is not in this conversation, call `day_briefing()` first.
1. In one `filesystem` call:
   - If the briefing says the note is not created yet, `write` its new-note text exactly as given, `<% %>` syntax included, as the Journals plugin does. If the note was created since, this `write` fails harmlessly.
   - `append` to the very bottom of the note: empty lines (so the markdown renders), the header `#### Agent's recommendation`, then the final morning reflection, energy level, intentions, and agreed tasks, keeping any `- [/]` items exactly as the user wrote them.
2. Confirm the saved path in one line. Log observations on the user's priorities or mood in your `<system_memory_log>`.