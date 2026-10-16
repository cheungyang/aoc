---
name: write_daily_intention
description: Generates the daily journal, resolves templates, appends intentions, and outputs IPC XML.
---
## Core
Log the agreed plan in today's daily note, `pkm/vault/journals/fleeting/YYYY-MM-DD.md` (always pass this exact relative path to `filesystem`).
1. `read` today's note. If it does not exist, `read` the template `pkm/templates/Fleeting Pages.md`, replace ALL Obsidian Templater syntax (`<% tp... %>`) with static values (e.g. `<% tp.date.now("w")-1 %>` becomes last week's ISO week number; date and title syntax becomes today's date), and `write` the result to today's path.
2. `append` to the very bottom of the note: empty lines (so the markdown renders), the header `#### Agent's recommendation`, then the final morning reflection, energy level, intentions, and agreed tasks, keeping any `- [/]` items exactly as the user wrote them.
3. Confirm the saved path in one line. Log observations on the user's priorities or mood in your `<system_memory_log>`.

Call `load_skill("write_daily_intention")` for the IPC response format, used only when another agent asked for this.

## Reference

### IPC response format
When another agent requested the daily intention, finish with exactly this structure:
```xml
<write_daily_intention_response>
  <original_request>[The user's prompt or the trigger event to write the intention]</original_request>
  <triggering_agent>[Agent ID or 'User']</triggering_agent>
  <payload>
    <intention_set>[The actual synthesized intention appended to the journal]</intention_set>
    <file_path>[The path of the file that was updated (e.g., pkm/vault/journals/fleeting/YYYY-MM-DD.md)]</file_path>
  </payload>
  <errors>[Any template resolution errors, sync conflicts, or 'None']</errors>
  <learnings>[Observations on the user's current project priorities or mood based on the agreed intentions]</learnings>
</write_daily_intention_response>
```