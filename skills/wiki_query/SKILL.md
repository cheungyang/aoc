---
name: wiki_query
description: Synthesizes answers by querying the vault via vector search and structured DBs, outputs XML, and archives syntheses.
---

## Overview
This skill acts as an intelligent "Second Brain" interface for the user's PKM. Instead of manually traversing index files, it utilizes `vault_search` (LanceDB Vector/BM25) to semantically retrieve relevant knowledge chunks, and queries structured SQLite databases (`task_query`, `project_query`) when the prompt involves active work or tasks. It synthesizes a comprehensive answer, outputs a standardized XML wrapper (IPC protocol) suitable for other agents, and archives complex syntheses back into the wiki as compounding knowledge.

## Boundaries & Guardrails
- **Strict Sourcing:** Base answers *exclusively* on the information returned from the search tools (`vault_search`, `task_query`, `project_query`).
- **Web Search Constraint:** Do NOT use the `web_search` tool unless you explicitly identify a critical knowledge gap and the user grants permission to search for it.
- **Formatting:** Output the final response strictly in the requested XML format.

## Workflow

### 1. Intelligent Retrieval
- Receive the query from the user or another agent.
- Analyze the prompt to determine the required context:
  - If the query seeks conceptual knowledge, past logs, or general information, use `vault_search` (with `category="wiki"` or `category="all"`) to retrieve highly relevant chunks.
  - If the query explicitly asks about active initiatives, statuses, or todos, execute `project_query` and/or `task_query`.
- You may run multiple queries if a prompt spans multiple domains.

### 2. Gap Identification & Permission
- Evaluate the retrieved chunks and database rows. Does this information fully answer the query?
- If there are critical gaps in the retrieved data, PAUSE and ask the user: *"My retrieval is complete, but I lack information on [Specific Gap]. Would you like me to perform a web search to fill this gap?"*
- If the user says "yes," execute the `web_search`. If "no," proceed with the knowledge you have.

### 3. Agent-Friendly Synthesis Output (IPC Format)
Generate the final response using the exact XML structure below to ensure readability and orchestration for routing agents.
- **Output Format Structure:**
  ```xml
  <wiki_query_response>
    <original_request>[The initial query or research question]</original_request>
    <triggering_agent>[Agent ID or 'User']</triggering_agent>
    <payload>
      <synthesis>
        [The comprehensive answer derived purely from the retrieval tools (and web search, if approved). 
        Must include inline markdown citations to the files (e.g., [Concept Name](../../concepts/Concept.md))]
      </synthesis>
      <sources_retrieved>[List of all files and DB rows utilized to build the synthesis]</sources_retrieved>
      <knowledge_gaps>[Any lingering questions or concepts missing from the system]</knowledge_gaps>
    </payload>
    <errors>[Any retrieval errors encountered, or 'None']</errors>
    <learnings>[Execution insights, observations on data gaps, optimal search terms for future use]</learnings>
  </wiki_query_response>
  ```
- **Memory Trigger:** Immediately after outputting the XML, use the `memory` skill to record the contents of the `<learnings>` tag so the system learns from this execution.

### 4. Archival (The Syntheses Directory)
Save valuable conceptual syntheses back into the wiki to compound the knowledge graph. (Note: Do not archive purely task-oriented summaries, only knowledge/topic syntheses).
- Use `obsidian` (`write`) to create `pkm/wiki/syntheses/[Topic_Name].md` (Ensure `Topic_Name` is concise and URL-safe).
- **Template:**
  ```markdown
  ---
  topic: <Topic Name>
  date_synthesized: YYYY-MM-DD
  ---
  ## Query
  <Original Query>
  
  ## Synthesis
  <The compiled answer with markdown links from the <synthesis> tag>
  
  ## Sources Retrieved
  <The list of sources from the <sources_retrieved> tag>
  ```
- **Update Index:** Use `obsidian` to `read`, modify, and `overwrite` `pkm/wiki/syntheses/index.md` by appending the following to the list:
  `[YYYY-MM-DD] [<Topic Name>](../../syntheses/<Topic_Name>.md) <one-line description of the synthesis>`

## Required Tools
- `vault_search`: Required to retrieve unstructured knowledge chunks via vector search.
- `task_query`: Required to retrieve structured task data.
- `project_query`: Required to retrieve structured project data.
- `obsidian`: Required to `read`, `write`, and `overwrite` synthesis files and indexes in the `pkm` vault.
- `web_search`: Required to fill knowledge gaps ONLY after explicit user approval.