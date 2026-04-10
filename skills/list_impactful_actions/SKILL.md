---
name: list_impactful_actions
description: Navigate the user's personal vault to identify the most impactful tasks and projects to facilitate daily/weekly planning.
---
## Overview
This skill guides the agent to navigate the user's personal PKM vault to identify the most impactful list of things to do for the day, week, or quarter. By synthesizing active projects, weekly goals, and urgent tasks, the agent helps facilitate effective planning.

## Boundaries & Constraints
- **Obsidian Call Limit**: You MUST strictly limit your `obsidian` tool calls to a maximum of 20 total per execution. Be efficient and precise with your searches.
- **Aggressive Structural Logging**: The PKM vault is large, but its structure is highly stable. You must aggressively log findings about project lists, folder structures, and metadata patterns into your agent's memory. This ensures that over time, tool call accuracy improves and fewer calls are needed.

## PKM Taxonomy Reference
You must understand the following taxonomy to accurately filter and prioritize data:
- **Priority Icons**: 🔺 Highest / ⏫ High / 🔼 Medium / 🔽 Low / ⏬ Lowest
- **Categories**: 💌 Gmail, 🦄 Personal, 🏠 Family, 💼 Work (implied context)
- **Tag Prefixes**: 
  - `#a/`: Actions
  - `#p/`: Projects (This matches the alias frontmatter at the top of a project note)
  - `#s/`: Status
  - `#c/`: Prioritized commitment for a specific year (e.g., `#c/⏫2026`)

## Workflow

### Phase 0: Sync Remote (Pre-Flight)
Always ensure the local vault is up to date before analyzing priorities.
- Use the `git` tool to perform a `pull` action on the `pkm` repository.

### Phase 1: Retrieve the Weekly Focus
1. Use your internal knowledge of the current date to calculate the correct ISO week number (Format: `YYYY-Www`).
2. Use the `obsidian` tool to `read` the previous week's journal located at `vault/pages/journals/weekly/YYYY-Www.md` (Note: You are reading *last week's* journal to find the goals set for *this week*).
3. Extract the items explicitly listed under the header `##🌱 Next week's focus`.

### Phase 2: Identify Active Projects
1. Determine the current year (`YYYY`).
2. Use the `obsidian` tool to search within the `vault/pages/projects` directory for projects tagged with the current year's commitment tag (e.g., `#c/⏫YYYY`).
3. **Crucial Filtering**: You MUST exclude any projects that contain the following status tags in their frontmatter or body:
   - `#s/⏸️Pause`
   - `#s/🟢Done`
   - `#s/🛑Discontinued`
   - `#s/🏝️Delegating`
4. Sort the remaining active projects by their Priority Icon (🔺 to ⏬).

### Phase 3: Retrieve, Prioritize Tasks & Track Momentum
1. For each active project identified in Phase 2, locate its file path and its alias tag (e.g., `#p/my-project`).
2. **Track Momentum**: Once the project list and paths are known, use the `git` tool's `log-p` action on the specific project files to quickly understand the delta between recent edits. Use these insights to gauge project progression and generate more precise, context-aware recommendations.
3. Use the `obsidian` tool's `vector_search` action to search the `ticktick/` directory using the project's alias tag as the query.
4. **Parse & Filter Tasks**:
   - Strictly filter the returned markdown lines for uncompleted tasks. An uncompleted task is defined by the exact markdown syntax: `- [ ]`. Ignore completed tasks (`- [x]`).
   - Evaluate the urgency of the uncompleted tasks by checking for any scheduled or due dates mentioned in the task line.
   - Evaluate the priority icons attached to the tasks.
5. Select the top 3 most impactful tasks that require immediate attention to move the project forward.

### Phase 4: Consolidate & Render Output
Compile the findings into clear, categorized buckets. 
1. Group the data into: **Work**, **Personal**, **Family**, and **Others** (using the category tags found on the projects).
2. Under each bucket, list the active projects.
3. Under each project, list the top 3 impactful tasks.
4. **Provide Reasoning**: Include a brief explanation of *why* these specific tasks were highlighted, referencing their priority, urgency, recent commit deltas (from Phase 3), or alignment with the Weekly Focus extracted in Phase 1.

### Phase 5: Structural Memory Logging
- Before concluding, review any newly discovered structural patterns, project paths, or taxonomy nuances.
- Load and execute the `memory` skill to log these structural findings into long-term memory, ensuring future executions remain within the 20-call limit.

## Required Tools
- `git`: Required to `pull` the latest changes from the remote `pkm` repository before scanning, and to use `log-p` to track project progress deltas.
- `obsidian`: Required to perform `read`, `file_search`, and `vector_search` actions within the `pkm` vault to access projects, tasks, and journals.