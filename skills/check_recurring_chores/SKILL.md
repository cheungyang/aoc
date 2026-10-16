---
name: check_recurring_chores
description: Analyzes pending recurring chores against historical completion patterns to determine if the user is falling behind their usual behavioral cadence.
---
## Overview
Flags pending recurring chores by the user's usual completion window, learned from past completions of the same chore, rather than by the hard deadline alone.

## Workflow

### Phase 1: Fetch Pending Tasks and History
Make both `task_query` calls (`action="sql"`) in the same step. Each query's LIMIT matches its `limit` argument; if the tool still reports rows not shown, raise both.
- Pending recurring tasks that have a due date, with `limit=50`:
  `SELECT title, raw_title, due_date FROM tasks WHERE status = 'todo' AND raw_title LIKE '%🔁%' AND due_date IS NOT NULL ORDER BY due_date LIMIT 50;`
- Past completions of recurring tasks, newest first, with `limit=100`:
  `SELECT title, completed_date, due_date FROM tasks WHERE status = 'completed' AND raw_title LIKE '%🔁%' AND completed_date IS NOT NULL ORDER BY completed_date DESC LIMIT 100;`

### Phase 2: Behavioral Analysis (Internal Reasoning)
For each pending task:
1. Find its matching historical records.
2. Calculate the "Delta": how many days *before* or *after* the `due_date` the user usually completed it.
3. Establish the "Usual Completion Window", e.g. completed between 17 and 52 days before the due date.
4. Compare today's date with that window and the task's `due_date`:
   - **Signal Trigger**: today is *inside* the window, or *past* it but before the hard deadline: flag it for a reminder.
   - **Silent Trigger**: today is *before* the window: ignore the task (do not remind the user yet).

### Phase 3: Output
List each flagged chore as `- [Task Name] (Due: YYYY-MM-DD) - Context: You usually complete this by [Date/Window].` (or `None`), plus how many chores stayed silent because it is too early.