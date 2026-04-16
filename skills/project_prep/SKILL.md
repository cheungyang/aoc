---
name: project_prep
description: Compiles living projects into the LLM Wiki by synthesizing goals, connecting concepts via wiki_query, and preserving verbatim actionable tasks.
---

## Overview
This skill acts as a dynamic project compiler and knowledge integrator. It pulls raw project files, maps their key entities and concepts to the broader knowledge graph using `wiki_query`, and extracts specific tasks from the `ticktick/` directory. It outputs a persistent, compiled project file in the LLM Wiki that preserves tasks strictly verbatim.

## Boundaries & Guardrails
- **Verbatim Task Preservation:** When searching `ticktick/`, extract the specific bullet points/lines that contain the exact `#p/` alias tag. **Do not summarize or alter these tasks.**
- **Tool Restrictions:** To analyze file history and deltas, you MUST exclusively use the `log-p` action of the `git` tool. Do not use direct `git diff` commands.
- **Link Formatting:** Use standard Markdown relative links for cross-linking concepts (e.g., `[Concept Name](../../concepts/Concept.md)`).
- **Do not modify sources:** Do not modify the original raw files in `vault/` or `ticktick/`.

## Workflow

### Phase 1: Gathering
1. Read the specified raw project file from `vault/pages/projects/` using the `obsidian` tool, `vault_id` must explicitly be `"pkm"`.
2. Locate the YAML frontmatter and extract the `alias:` field value that begins with `#p/` (e.g., `#p/my-project`).
3. Read all filenames in the `ticktick/` directory using the `obsidian` tool `file_search` actiom with `.md` as search term, then read each file in ticktick to get a list of all tasks. Extract the tasks that has the alias tag (e.g., `#p/my-project`). Keep *all* matching tasks exactly verbatim. 

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
     <Exact, verbatim tasks extracted from ticktick, categorized by milestones or groupings>
     ```
   - Update `wiki/projects/index.md` by appending: `[YYYY-MM-DD] [<Project Name>](<project_name>.md) <one-line summary>`.

### Phase 4: Continuous Sync & Velocity (Existing Projects)
8. If the project already exists in `wiki/projects/`, perform an "Update & Sync":
   - Use the `git` tool (`log-p` action) on the raw file in `vault/pages/projects/` to extract the recent "delta" (new goals, narrative shifts, new concepts).
   - If the delta contains new entities/concepts, re-trigger `wiki_query` and synthesize their impact on the project.
   - `overwrite` `wiki/projects/<project_name>.md` with the updated compilation (refreshing goals, adding new concept links, and replacing the tasks block with the latest verbatim state of tasks from `ticktick/`).
   - Use the `git` tool (`log-p` action) on the `ticktick/` directory to measure task velocity (e.g., completions from `- [ ]` to `- [x]`).
   - Return a summary of the new concepts integrated and the task velocity to the calling agent.

## Required Tools
- `obsidian`: Required to read raw files, search and read `ticktick/`, and create/overwrite wiki files.
- `git`: Required to use the `log-p` action for detecting project deltas and task velocity.