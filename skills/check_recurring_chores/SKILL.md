---
name: check_recurring_chores
description: Analyzes pending recurring chores against historical completion patterns to determine if the user is falling behind their usual behavioral cadence.
---
## Overview
This skill queries uncompleted recurring chores and compares them against the user's historical completion dates for those exact tasks. Instead of relying purely on a hard deadline, it establishes a "usual completion window" based on the user's past behavior.

## Workflow

### Phase 1: Fetch Pending Recurring Tasks
- Use the `task_query` tool (`action="sql"`) to retrieve pending recurring tasks that have a due date:
  `SELECT raw_title, due_date FROM tasks WHERE status = 'todo' AND raw_title LIKE '%🔁%' AND due_date IS NOT NULL;`

### Phase 2: Fetch Historical Data
- Use the `task_query` tool (`action="sql"`) to fetch past completion records for recurring tasks:
  `SELECT raw_title, completed_date, due_date FROM tasks WHERE status = 'completed' AND raw_title LIKE '%🔁%' AND completed_date IS NOT NULL;`

### Phase 3: Behavioral Analysis (Internal Reasoning)
For each pending task identified in Phase 1:
1. Find the matching historical records from Phase 2.
2. Calculate the historical "Delta": How many days *before* or *after* the `due_date` did the user usually complete this task?
3. Establish the "Usual Completion Window". For example, if historically completed between 17 and 52 days before the due date, that is the window.
4. Compare today's date against that window and the pending task's `due_date`.
   - **Signal Trigger**: If today's date is *inside* the usual completion window, or *past* the usual completion window (but before the hard deadline), flag it for a reminder. 
   - **Silent Trigger**: If today's date is *before* the usual completion window, ignore the task (do not remind the user yet).

### Phase 4: Output (IPC Format)
Output the flagged chores in the strict XML format below. 
```xml
<recurring_chores_response>
  <flagged_chores>
    - [Task Name] (Due: YYYY-MM-DD) - Context: You usually complete this by [Date/Window].
  </flagged_chores>
  <silent_chores_count>[Number of chores ignored because it is too early]</silent_chores_count>
</recurring_chores_response>
```
If no chores are flagged, output `<flagged_chores>None</flagged_chores>`.

## Required Tools
- `task_query`: Required to execute the SQL queries against the tasks database.