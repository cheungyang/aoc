---
name: wiki_query
description: Traverses the LLM Wiki (up to 10 hops) to answer queries, synthesizes findings into an IPC XML format, and archives complex syntheses.
---

## Overview
This skill acts as a "Second Brain" interface for the LLM Wiki. Instead of using transient RAG search, the agent reads the wiki indexes, intelligently follows Markdown links across up to 10 files (graph hopping), and synthesizes a comprehensive answer. It outputs a standardized XML wrapper (IPC protocol) suitable for other agents and archives the result back into the wiki as compounding knowledge.

## Boundaries & Guardrails
- **Strict Sourcing:** Base answers *exclusively* on the information found within the wiki files.
- **Web Search Constraint:** Do NOT use the `web_search` tool unless you explicitly identify a knowledge gap and the user grants permission to search for it.
- **Hop Limit:** Do NOT read more than 10 distinct files during the traversal phase to conserve context window.
- **Formatting:** Output the final response strictly in the requested XML format.

## Workflow

### 1. Query Analysis & Index Scanning
- Receive the query from the user or another agent.
- Use the `obsidian` tool (`read`) to read the following index files:
  - `pkm/wiki/concepts/index.md`
  - `pkm/wiki/entities/index.md`
  - `pkm/wiki/summaries/index.md`
  - `pkm/wiki/syntheses/index.md`
- Based on the one-line descriptions in these indexes, identify the most relevant files to begin your search.

### 2. Deep Traversal (The 10-Hop Loop)
- Use `obsidian` (`read`) to read the initial files identified.
- **Graph Hopping:** Scan the content of these files for Markdown links (e.g., `[Text](path)`). If a linked page seems relevant to the query, read it.
- Maintain a running tally of files read. Stop traversal when you hit the **10-hop maximum**, or when you have sufficient information to answer the query comprehensively.

### 3. Gap Identification & Permission
- Evaluate your findings. Does the wiki fully answer the query?
- If there are critical gaps, PAUSE and ask the user: *"My wiki traversal is complete, but I lack information on [Specific Gap]. Would you like me to perform a web search to fill this gap?"*
- If the user says "yes," execute the `web_search`. If "no," proceed with the knowledge you have.

### 4. Agent-Friendly Synthesis Output (IPC Format)
Generate the final response using the exact XML structure below to ensure readability and orchestration for routing agents.
- **Output Format Structure:**
  ```xml
  <wiki_query_response>
    <original_request>[The initial query or research question]</original_request>
    <triggering_agent>[Agent ID or 'User']</triggering_agent>
    <payload>
      <synthesis>
        [The comprehensive answer derived purely from the wiki (and web search, if approved). 
        Must include inline markdown citations to the wiki files (e.g., [Concept Name](../../concepts/Concept.md))]
      </synthesis>
      <sources_traversed>[List of all wiki files read during the traversal]</sources_traversed>
      <knowledge_gaps>[Any lingering questions or concepts missing from the wiki]</knowledge_gaps>
    </payload>
    <errors>[Any missing files, broken links encountered during traversal, etc., or 'None']</errors>
    <learnings>[Execution insights, notes on wiki health discovered during traversal, optimal hop paths]</learnings>
  </wiki_query_response>
  ```
- **Memory Trigger:** Immediately after outputting the XML, use the `memory` skill to record the contents of the `<learnings>` tag so the system learns from this execution.

### 5. Archival (The Syntheses Directory)
Save the valuable synthesis back into the wiki to compound the knowledge graph.
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
  
  ## Sources Traversed
  <The list of links from the <sources_traversed> tag>
  ```
- **Update Index:** Use `obsidian` to `read`, modify, and `overwrite` `pkm/wiki/syntheses/index.md` by appending the following to the list:
  `[YYYY-MM-DD] [<Topic Name>](../../syntheses/<Topic_Name>.md) <one-line description of the synthesis>`

## Required Tools
- `obsidian`: Required to `read` index files and content files during traversal, and to `write`/`overwrite` synthesis files and indexes in the `pkm` vault.
- `web_search`: Required to fill knowledge gaps ONLY after explicit user approval.