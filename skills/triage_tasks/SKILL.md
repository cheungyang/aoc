---
name: triage_tasks
description: Systematically processes and tags untriaged tasks in the vault, interacting with the user for priority.
---
## Overview
This skill guides the agent to systematically process untriaged tasks in the vault. It strictly uses existing action (`#a/`) and project (`#p/`) tags, presenting tasks one by one to the user for priority selection via interactive buttons.

**Token discipline:** never load the whole tag universe or dump task/project lists. Load one compact index of action tags once, then look up project-tag candidates **per task** with the targeted queries below. Tags are stored in `tasks.db` / `projects.db` as JSON arrays **without** the `#` (e.g. `["a/learn", "p/aoc"]`); add the `#` only when writing to markdown.

## Workflow

### Phase 1: Context Initialization (Runs once per session)
1. **Action-tag index (compact).** Run ONE `task_query` with `action="sql"`, `limit=200`:
   ```sql
   SELECT j.value AS tag, COUNT(*) AS n FROM tasks, json_each(tasks.tags) AS j WHERE j.value LIKE 'a/%' GROUP BY j.value ORDER BY n DESC
   ```
   This returns only distinct `a/` tags with usage counts — the closed verb vocabulary. Keep it for the whole session.
2. **Do NOT pre-load project tags.** Project candidates are fetched per task in Phase 2 Step 2.
3. Your long-term memory (Profile, shared topics, your memory and feedback) is already in your system prompt. Apply any tagging rules found there; do not re-read those files.

### Phase 2: The Triage Loop
Keep a session list of skipped task ids (initially empty).

1. **Query:** Use `task_query` with `action="sql"`, `limit=1` to fetch exactly ONE task that lacks an `a/` tag OR lacks a `p/` tag, AND has no priority. Replace `<SKIPPED_IDS>` with the quoted, comma-separated skipped ids (use `''` when none):
   ```sql
   SELECT id, title, tags, source, line_number, raw_line FROM tasks WHERE status = 'todo' AND (priority IS NULL OR priority = '') AND (tags IS NULL OR tags NOT LIKE '%"a/%' OR tags NOT LIKE '%"p/%') AND id NOT IN (<SKIPPED_IDS>) ORDER BY source, line_number LIMIT 1
   ```
   - If no such tasks are found, output: "Inbox Zero! All tasks triaged." and terminate the skill.
2. **Find candidate tags (scoped to THIS task only):** Skip whichever half the task already has.
   - **Action tag:** pick from the Phase 1 index based on the task's verb.
   - **Project tag — run in this order, stop as soon as you have a strong match:**
     a. *Same-file precedent* — `task_query`, `action="sql"`, `limit=5` (replace `<SOURCE>` with the task's `source`; double any `'`):
        ```sql
        SELECT j.value AS tag, COUNT(*) AS n FROM tasks, json_each(tasks.tags) AS j WHERE tasks.source = '<SOURCE>' AND j.value LIKE 'p/%' GROUP BY j.value ORDER BY n DESC
        ```
     b. *Keyword match* — for 1–3 distinctive nouns in the task title, call `project_query` with `action="search"`, `query="<noun>"`, `status="all"`, `limit=5`. Use the returned `name`/`category`/`tags` for semantic context (e.g. `[#p/glm : "Global Logistics Module"]`).
     c. *Existence check* — before proposing any `p/` tag that did not come from (a) or (b), confirm it is in use (`task_query`, `action="sql"`, `limit=1`):
        ```sql
        SELECT j.value AS tag, COUNT(*) AS n FROM tasks, json_each(tasks.tags) AS j WHERE j.value = '<TAG>' GROUP BY j.value
        ```
        If it returns nothing, do not use it.
3. **Analyze:** Select the best action tag based on the verb. Select a project tag only if there is a strong match from Step 2. If no project fits, leave it blank. DO NOT invent new tags.
4. **Present:** Display the task in Discord using this exact minimal format:
   > **Task:** `[ ] Task title`
   > **Proposed:** `#a/...` `#p/...` (or No project)
5. **Poll:** Output a `<poll>` for priority selection using the exact interactive options: `🔺`, `⏫`, `🔼`, `🔽`, `⏬`, and `Skip` (use emoji ⏭️ for skip).
6. **Listen & Learn (Wait for user feedback):**
   - **If the user clicks a priority button:** Use `filesystem` to `replace_block` in the original markdown file (`source`, matching `raw_line`) to inject the selected priority and your proposed tags into the task string. Then instantly loop back to Phase 2, Step 1.
   - **If the user types a text correction (e.g. "Make it #p/work"):** Log the correction into your long-term memory via a `<system_memory_log>` block so the `dream` routine makes it permanent. Apply the user's manual tags, and reprompt for the priority buttons.
   - **If the user clicks Skip:** Add the task id to the skipped list (so Step 1 does not return it again today) and loop back to Phase 2, Step 1.

## Rules & Boundaries
- NEVER hallucinate or invent new `#a/` or `#p/` tags.
- NEVER run an unscoped query that lists every task, every project, or every `p/` tag.
- Always pass an explicit `limit` to `task_query`/`project_query` (the tool default is small and would silently truncate the Phase 1 index).
- The loop stops only when the query in Phase 2 Step 1 returns 0 tasks.
- Keep output text strictly to the minimal format requested.

## Required Tools
- `task_query`: Required to query for untriaged tasks, the compact `a/` tag index, and per-task `p/` tag precedent.
- `project_query`: Required for per-task keyword lookup of candidate projects and their contextual details.
- `filesystem`: Required to execute `replace_block` in the `pkm/` directory to update the task lines in markdown files.