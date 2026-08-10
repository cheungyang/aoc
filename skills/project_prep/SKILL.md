---
name: project_prep
description: Compiles living projects into the LLM Wiki by synthesizing goals, connecting concepts via wiki_query, and preserving verbatim actionable tasks.
---

## Overview
This skill acts as a dynamic project compiler and knowledge integrator. It pulls raw project files, maps their key entities and concepts to the broader knowledge graph using `wiki_query`, and extracts specific tasks dynamically from the SQLite task cache. It outputs a persistent, compiled project file in the LLM Wiki that preserves tasks strictly verbatim.

## Boundaries & Guardrails
- **Zero Filesystem Task Discovery:** You MUST NOT use `file_search` to find tasks. Rely purely on the `task_query` tool.
- **Verbatim Task Preservation:** When querying for tasks, you must extract and compile them exactly verbatim. **Do not summarize or alter these tasks.**
- **Tool Restrictions:** To analyze file history and deltas, you MUST exclusively use the `log-p` action of the `git` tool. Do not use direct `git diff` commands.
- **Link Formatting:** Use standard Markdown relative links for cross-linking concepts (e.g., `[Concept Name](../../concepts/Concept.md)`).
- **Do not modify sources:** Do not modify the original raw files in `vault/` or `ticktick/`.

## Workflow

### Phase 1: Gathering
1. Use `project_query` to get the metadata for the requested project, or read the specified raw project file from `vault/projects/` using the `obsidian` tool (vault_id="pkm").
2. Locate the YAML frontmatter and identify the `alias:` field value that begins with `#p/` (e.g., `#p/my-project`).
3. Use the `task_query` tool to retrieve tasks associated with this project. Tasks belong to a project in two distinct forms:
   - **Form A (Internal Tasks):** Tasks that are located directly within the project's own markdown file. Retrieve these by using `task_query` with `source="<project_file_path>"`.
   - **Form B (External Tasks):** Tasks located outside the project markdown (e.g., in a `ticktick/` inbox) but containing the alias tag. Retrieve these by using `task_query` with `tags=["p/<project_alias>"]`.
   Query both sets, merge the results, and keep all matching tasks exactly verbatim.

### Phase 2: Knowledge Graph Connectivity
4. Analyze the raw project text to identify key Entities and abstract Concepts.
5. Trigger the `wiki_query` skill to ask: *"What do we already know about [Entity/Concept] in relation to this project domain?"*
6. Synthesize the findings to establish how this project connects to the existing system knowledge graph.

### Phase 3: Compilation & Indexing (New Projects)
7. Check if the project exists in `wiki/projects/index.md`. If it is **New**:
   - Create `wiki/projects/<project_name>.md` using the `obsidian` tool.
   - Use the following template:
     ```markdown
     ---
     project_name: <Name>
     date_compiled: YYYY-MM-DD
     ---
     ## Goals & Plan
     <Summarized extraction of the project's core objectives>

     ## Knowledge Graph & Context
     <Markdown links to existing Entities/Concepts discovered via wiki_query, and a brief synthesis of how they relate>

     ## Project Tasks
     <Exact, verbatim tasks extracted from task_query, categorized by internal vs external, or by milestones>
     ```
   - Update `wiki/projects/index.md` by appending: `[YYYY-MM-DD] [<Project Name>](<project_name>.md) <one-line summary>`.

### Phase 4: Continuous Sync & Velocity (Existing Projects)
8. If the project already exists in `wiki/projects/`, perform an "Update & Sync":
   - Use the `git` tool (`log-p` action) on the raw file in `vault/projects/` to extract the recent "delta" (new goals, narrative shifts, new concepts).
   - If the delta contains new entities/concepts, re-trigger `wiki_query` and synthesize their impact on the project.
   - `overwrite` `wiki/projects/<project_name>.md` with the updated compilation (refreshing goals, adding new concept links, and replacing the tasks block with the latest verbatim state of tasks retrieved from `task_query`).
   - Evaluate task velocity by comparing the completed vs uncompleted tasks returned by `task_query` (e.g., checking for recently completed items).
   - Return a summary of the new concepts integrated and the task velocity to the calling agent.

## Required Tools
- `obsidian`: Required to read raw files and create/overwrite wiki files.
- `git`: Required to use the `log-p` action for detecting project deltas.
- `task_query` & `project_query`: Required for retrieving strict, stateful metadata and project tasks.