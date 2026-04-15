---
name: list_impactful_actions
description: Navigates the personal vault to identify impactful tasks, outputting an IPC XML plan.
---
## Overview
This skill guides the agent to navigate the user's personal PKM vault to identify the most impactful list of things to do for the day, week, or quarter. By synthesizing active projects, weekly goals, and urgent tasks, the agent helps facilitate effective planning, finalizing with a standard IPC XML output.

## Boundaries & Constraints
- **Obsidian Call Limit**: You MUST strictly limit your `obsidian` tool calls to a maximum of 20 total per execution. Be efficient and precise with your searches.
- **Aggressive Structural Logging**: The PKM vault is large, but its structure is highly stable. You must aggressively log findings about project lists, folder structures, and metadata patterns into your agent's memory. This ensures that over time, tool call accuracy improves and fewer calls are needed.
- **Formatting**: The final output MUST strictly adhere to the requested IPC XML structure.

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
3. Use the `vector_search` tool with the project's alias tag as the query to find relevant tasks.
4. **Parse & Filter Tasks**:
   - Strictly filter the returned markdown lines for uncompleted tasks. An uncompleted task is defined by the exact markdown syntax: `- [ ]`. Ignore completed tasks (`- [x]`).
   - Evaluate the urgency of the uncompleted tasks by checking for any scheduled or due dates mentioned in the task line.
   - Evaluate the priority icons attached to the tasks.
5. Select the top 3 most impactful tasks that require immediate attention to move the project forward.

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
      [Brief list of files/directories read and analyzed (e.g., vault/pages/journals/weekly/YYYY-Wxx.md, specific project files)]
    </sources_analyzed>
    <git_sync_status>[Status of the Pre-Flight pull]</git_sync_status>
  </payload>
  <errors>[Any missing files, vector search failures, or 'None']</errors>
  <learnings>[Observations on the user's workload balance, stalled projects, or newly discovered structural PKM patterns]</learnings>
</list_impactful_actions_response>
```
**Memory Trigger**: Immediately after outputting the XML, use the `memory` skill to record the contents of the `<learnings>` tag so the system learns from this execution and maintains the 20-call limit.

## Required Tools
- `git`: Required to `pull` the latest changes from the remote `pkm` repository before scanning, and to use `log-p` to track project progress deltas.
- `obsidian`: Required to perform `read` and `file_search` actions within the `pkm` vault to access projects, tasks, and journals.
- `vector_search`: Required to perform vector operations on the vault.