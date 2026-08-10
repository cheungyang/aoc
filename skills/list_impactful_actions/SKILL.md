---
name: list_impactful_actions
description: Navigates the personal databases and vault to identify impactful tasks, outputting an IPC XML plan.
---
## Overview
This skill guides the agent to query the user's SQLite databases (`tasks.db`, `projects.db`) and PKM vault to identify the most impactful list of things to do for the day, week, or quarter. By synthesizing active projects, weekly goals, and urgent tasks without burning tokens on filesystem searches, the agent helps facilitate effective planning.

## Boundaries & Constraints
- **Zero Filesystem Discovery**: You MUST NOT use `obsidian` tool for discovering tasks or projects. You must rely purely on `task_query` and `project_query`.
- **Obsidian Call Limit**: You are permitted to use `obsidian.read` strictly to read the weekly journal or to investigate specific file paths retrieved from the DBs.
- **Formatting**: The final output MUST strictly adhere to the requested IPC XML structure.

## PKM Taxonomy Reference
You must understand the following taxonomy to accurately filter and prioritize data:
- **Priority Icons**: 🔺 Highest / ⏫ High / 🔼 Medium / 🔽 Low / ⏬ Lowest
- **Categories**: 💌 Gmail, 🦄 Personal, 🏠 Family, 💼 Work (implied context)
- **Tag Prefixes**: 
  - `#a/`: Actions
  - `#p/`: Projects (This matches the alias frontmatter at the top of a project note)
  - `#c/`: Prioritized commitment for a specific year (e.g., `#c/⏫2026`)

## Workflow

### Phase 0: Sync Remote (Pre-Flight)
Always ensure the local vault is up to date before analyzing priorities.
- Use the `git` tool to perform a `pull` action on the `pkm` repository.
- Execute `project_query` (`action="sync"`) and `task_query` (`action="sync"`) if required by the system state.

### Phase 1: Retrieve the Weekly Focus
1. Use your internal knowledge of the current date to calculate the correct ISO week number (Format: `YYYY-Www`).
2. Use the `obsidian` tool to `read` the previous week's journal located at `vault/journals/weekly/YYYY-Www.md` from vault_id `pkm` (Note: You are reading *last week's* journal to find the goals set for *this week*).
3. Extract the items explicitly listed under the header `##🌱 Next week's focus`.

### Phase 2: Identify Active Projects (DB First)
1. Use the `project_query` tool (`action="search"`) to retrieve projects.
2. Filter for projects where status is active (e.g., status='executing' or status='planning') and match the current `commitment_year`.
3. Sort the returned active projects by their Priority Icon (🔺 to ⏬).
4. Extract the project IDs (or aliases like `#p/my-project`) and their corresponding file paths.

### Phase 3: Retrieve, Prioritize Tasks & Track Momentum
1. **Fetch Tasks**: Use the `task_query` tool (`action="search"`, `status="todo"`) to pull all uncompleted tasks related to the active project aliases extracted in Phase 2.
2. **Track Momentum (Optional context)**: Use the `git` tool's `log-p` action on the specific project file paths to quickly understand the delta between recent edits and gauge momentum.
3. **Prioritize**: Evaluate the urgency of the tasks returned by `task_query` by checking for any scheduled or due dates, and evaluate their priority icons.
4. Select the top 3 most impactful tasks that require immediate attention to move the active projects forward.
5. **Deep Context (If Needed)**: If a selected task lacks sufficient context to be actionable, you MUST NOT guess. Call the `knowledge-retriever` agent (Rick) via IPC to retrieve the context for that specific project or task.

### Phase 4: Agent-Friendly Output & Memory (IPC Format)
Compile the findings and finalize the execution using the strict XML structure below. This format ensures perfect readability for routing agents.
```xml
<list_impactful_actions_response>
  <original_request>[The trigger or request to list impactful actions]</original_request>
  <triggering_agent>[Agent ID or 'User']</triggering_agent>
  <payload>
    <recommended_actions>
      [The markdown list of tasks grouped by Work, Personal, Family, Others. Includes reasoning for selection based on momentum and priority.]
    </recommended_actions>
    <sources_analyzed>
      [Brief list of files/directories read and analyzed (e.g., vault/journals/weekly/YYYY-Wxx.md, specific project files)]
    </sources_analyzed>
    <git_sync_status>[Status of the Pre-Flight pull]</git_sync_status>
  </payload>
  <errors>[Any missing files, DB failures, or 'None']</errors>
  <learnings>[Observations on the user's workload balance, stalled projects, or newly discovered structural PKM patterns]</learnings>
</list_impactful_actions_response>
```
**Memory Trigger**: Immediately after outputting the XML, use the `memory` skill to record the contents of the `<learnings>` tag so the system learns from this execution.

## Required Tools
- `git`: Required to `pull` the latest changes from the remote `pkm` repository before scanning, and to use `log-p` to track project progress deltas.
- `project_query`: Required to query active projects efficiently.
- `task_query`: Required to query pending tasks efficiently.
- `obsidian`: Permitted ONLY to perform `read` actions on journals or specific target paths.
