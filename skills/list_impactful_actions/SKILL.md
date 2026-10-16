---
name: list_impactful_actions
description: Navigates the personal databases and vault to identify the most impactful tasks.
---
## Overview
Find the most impactful things to do for the day, week, or quarter from `tasks.db`, `projects.db`, and the weekly journal.

## Rules
- Discover tasks and projects only with `task_query` and `project_query`, never by searching the filesystem. Use `filesystem` `read` only for the weekly journal or a path the databases returned.
- Priority icons: 🔺 Highest, ⏫ High, 🔼 Medium, 🔽 Low, ⏬ Lowest. Categories: 💌 Gmail, 🦄 Personal, 🏠 Family, 💼 Work (implied). Tags: `#a/` actions; `#p/` projects (the alias in a project note's frontmatter); `#c/` a year's prioritized commitment (e.g. `#c/⏫2026`).

## Workflow
1. In one step:
   - `read` last week's journal, `pkm/vault/journals/weekly/YYYY-Www.md` (the ISO week before the current one): the items under `##🌱 Next week's focus` are this week's goals.
   - `project_query` (`action="search"`): active projects (`executing` or `planning`) for the current `commitment_year`. Sort them by priority icon and note each one's alias and file path.
2. `task_query` (`action="search"`, `status="todo"`) for those projects' aliases. Optionally run `git` `log-p` on a project's file to gauge recent momentum.
3. Pick the top 3 tasks that most need attention to move active projects forward, judged by their due or scheduled dates and priority icons. If a pick lacks the context to act on, do not guess: read its project file.
4. Return the picks grouped by Work, Personal, Family, Others, each with why it was chosen (priority, momentum, this week's focus), and the sources you read. Note workload balance, stalled projects, or new PKM patterns in your `<system_memory_log>`.
