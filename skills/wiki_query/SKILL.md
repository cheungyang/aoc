---
name: wiki_query
description: Synthesizes answers by querying the vault via vector search and structured DBs, outputs XML, and archives syntheses.
---

## Core

Answer questions from the user's PKM with retrieval tools instead of walking index files, and archive valuable syntheses back into the wiki.

### Guardrails
- **Strict Sourcing:** Base answers *exclusively* on the information returned from the search tools (`vault_search`, `task_query`, `project_query`).
- **Web Search Constraint:** Do NOT use the `web_search` tool unless you explicitly identify a critical knowledge gap and the user grants permission to search for it.
- **Formatting:** Output the final response strictly in the XML format below.

### Workflow
1. **Retrieve.** Run several queries, in parallel, if the prompt spans domains:
   - Conceptual knowledge, past logs, general information: `vault_search` (`category="wiki"` or `category="all"`).
   - Exact string matches, specific names, code snippets: `filesystem` `grep`.
   - Active initiatives, statuses, todos: `project_query` and/or `task_query`.
2. **Gaps & permission.** If critical gaps remain, PAUSE and ask: *"My retrieval is complete, but I lack information on [Specific Gap]. Would you like me to perform a web search to fill this gap?"* Search only on "yes"; otherwise proceed with the knowledge you have.
3. **Output** this XML:
   ```xml
   <wiki_query_response>
     <original_request>[The initial query or research question]</original_request>
     <triggering_agent>[Agent ID or 'User']</triggering_agent>
     <payload>
       <synthesis>[The answer, derived purely from the retrieval tools (and web search, if approved), with inline markdown citations to the files (e.g., [Concept Name](../../concepts/Concept.md))]</synthesis>
       <sources_retrieved>[List of all files and DB rows utilized to build the synthesis]</sources_retrieved>
       <knowledge_gaps>[Any lingering questions or concepts missing from the system]</knowledge_gaps>
     </payload>
     <errors>[Any retrieval errors encountered, or 'None']</errors>
     <learnings>[Execution insights, observations on data gaps, optimal search terms for future use]</learnings>
   </wiki_query_response>
   ```
   Immediately after the XML, append a `<system_memory_log>` block recording the `<learnings>`.
4. **Archive** valuable knowledge/topic syntheses (never purely task-oriented summaries) to `pkm/wiki/syntheses/[Topic_Name].md` (concise, URL-safe name), and add a line to `pkm/wiki/syntheses/index.md`.

Call `load_skill("wiki_query")` for the synthesis file template and index line before archiving (step 4).

## Reference

### Overview
This skill acts as an intelligent "Second Brain" interface for the user's PKM. Instead of manually traversing index files, it utilizes `vault_search` (LanceDB Vector/BM25) to semantically retrieve relevant knowledge chunks, and queries structured SQLite databases (`task_query`, `project_query`) when the prompt involves active work or tasks. It synthesizes a comprehensive answer, outputs a standardized XML wrapper (IPC protocol) suitable for other agents, and archives complex syntheses back into the wiki as compounding knowledge.

### Archival (The Syntheses Directory)
Save valuable conceptual syntheses back into the wiki to compound the knowledge graph. (Note: Do not archive purely task-oriented summaries, only knowledge/topic syntheses).
- Use `filesystem` (`write`) to create `pkm/wiki/syntheses/[Topic_Name].md` (Ensure `Topic_Name` is concise and URL-safe).
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
- **Update Index:** Use `filesystem` (`append` action) to add the following to the bottom of `pkm/wiki/syntheses/index.md`:
  `[YYYY-MM-DD] [<Topic Name>](../../syntheses/<Topic_Name>.md) <one-line description of the synthesis>`

### Required Tools
- `vault_search`: Required to retrieve unstructured knowledge chunks via vector search.
- `task_query`: Required to retrieve structured task data.
- `project_query`: Required to retrieve structured project data.
- `filesystem`: Required to `grep` for exact strings, `write` synthesis files, and `append` to index files in the `pkm` vault.
- `web_search`: Required to fill knowledge gaps ONLY after explicit user approval.