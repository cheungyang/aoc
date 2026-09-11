---
name: triage_tasks
description: Systematically processes and tags untriaged tasks in the vault, interacting with the user for priority.
---
## Overview
This skill guides the agent to systematically process untriaged tasks in the vault. It strictly uses existing action (`#a/`) and project (`#p/`) tags, presenting tasks one by one to the user for priority selection via interactive buttons.

## Workflow

### Phase 1: Context Initialization (Runs once per session)
1. Use `task_query` (with SQL if needed) and `project_query` to fetch a master list of all currently used `#a/...` (action) and `#p/...` (project) tags. 
2. Ensure you fetch project names/categories for semantic context (e.g., `[#p/glm : "Global Logistics Module"]`).
3. Review your long-term memory (`CONTEXT.md`) for any past tagging rules learned from the user.

### Phase 2: The Triage Loop
1. **Query:** Use `task_query` to find exactly ONE task that lacks an `#a/` tag OR lacks a `#p/` tag, AND lacks a priority tag (`🔺`, `⏫`, `🔼`, `🔽`, `⏬`).
   - If no such tasks are found, output: "Inbox Zero! All tasks triaged." and terminate the skill.
2. **Analyze:** Analyze the retrieved task. Select the best action tag based on the verb. Select a project tag if there is a strong semantic match to an existing project (using your Phase 1 context). If no project fits, leave it blank. DO NOT invent new tags.
3. **Present:** Display the task in Discord using this exact minimal format:
   > **Task:** `[ ] Task title`
   > **Proposed:** `#a/...` `#p/...` (or No project)
4. **Poll:** Output a `<poll>` for priority selection using the exact interactive options: `🔺`, `⏫`, `🔼`, `🔽`, `⏬`, and `Skip` (use emoji ⏭️ for skip). 
5. **Listen & Learn (Wait for user feedback):**
   - **If the user clicks a priority button:** Use `filesystem` to `replace_block` in the original markdown file to inject the selected priority and your proposed tags into the task string. Then instantly loop back to Phase 2, Step 1.
   - **If the user types a text correction (e.g. "Make it #p/work"):** Log the correction into your long-term memory via a `<system_memory_log>` block so the `dream` routine makes it permanent. Apply the user's manual tags, and reprompt for the priority buttons.
   - **If the user clicks Skip:** Ignore the task for today and loop back to Phase 2, Step 1.

## Rules & Boundaries
- NEVER hallucinate or invent new `#a/` or `#p/` tags.
- The loop stops only when the query in Phase 2 Step 1 returns 0 tasks.
- Keep output text strictly to the minimal format requested.

## Required Tools
- `task_query`: Required to query for untriaged tasks and gather existing `#a/` tags.
- `project_query`: Required to query for existing `#p/` tags and their contextual details.
- `filesystem`: Required to execute `replace_block` in the `pkm/` directory to update the task lines in markdown files.