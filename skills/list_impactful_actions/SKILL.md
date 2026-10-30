---
name: list_impactful_actions
description: Navigates the personal databases and vault to identify the most impactful tasks.
---
## Overview
Find the most impactful things to do for the day, week, or quarter from this week's focus and the active projects' open tasks.

## Rules
- Today's briefing (the morning findings, or `day_briefing()`) holds this week's focus, this year's active projects by priority with their top open tasks, and the tasks due or slipping, with every day count computed. Work from it; don't redo those reads or the date math.
- For more of a project's tasks, use `task_query` (`action="search"`, `status="todo"`, `tags=[<alias>]`); for other projects or years, `project_query`. Never search the filesystem for tasks. Use `filesystem` `read` only for a path the briefing or the databases returned.
- Priority icons: 🔺 Highest, ⏫ High, 🔼 Medium, 🔽 Low, ⏬ Lowest. Categories come with each project (the briefing and `project_query` return them); a project with none is work. Tags: `#a/` actions; `#p/` projects (the alias in a project note's frontmatter); `#c/` a year's prioritized commitment (e.g. `#c/⏫2026`).

## Workflow
1. If today's briefing is not in this conversation, call `day_briefing()`, asking for `medium` thinking: choosing from the briefing is the heavy step. Optionally, in the same step, run `git` `log-p` on a project's file to gauge recent momentum.
2. Pick the top 3 tasks that most need attention to move active projects forward, judged by their due or scheduled dates, priority icons and this week's focus. If a pick lacks the context to act on, do not guess: read its project file.
3. Return the picks grouped by Work, Personal, Family, Others, each with why it was chosen (priority, momentum, this week's focus), and the sources you read. Note workload balance, stalled projects, or new PKM patterns in your `<system_memory_log>`.
