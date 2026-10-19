---
name: check_recurring_chores
description: Flags recurring chores that are overdue, due today, or inside the user's usual completion window, learned from past completions.
---
## Overview
Flags pending recurring chores by the user's usual completion window, learned from past completions of the same chore, rather than by the hard deadline alone.

## Workflow
1. Use the "Recurring chores" section of today's briefing when it is in this conversation; otherwise call `recurring_chores_due()`. Either way the window math and day counts are done.
2. Relay each flagged chore (overdue, due today, due soon) with its date, the days left or overdue, and how early or late the user usually does it, as given; say `None` when nothing is flagged. Add how many chores stayed quiet because it is too early. Never recompute a date or a count. Name any chore it couldn't judge (no date), so the user can add one.
3. If the tool fails, say so in one line; `task_query` still lists the 🔁 tasks with their dates, but not their windows.