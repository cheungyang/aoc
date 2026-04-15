---
name: wiki_ingest
description: Systematically processes a reading backlog, extracts/discusses entities and concepts, and compiles a dense, markdown-linked LLM Wiki graph. Outputs agent-friendly XML.
---

## Overview
This skill implements the "LLM Wiki" pattern. It systematically reads articles from a backlog, extracts specific Entities and abstract Concepts, engages the user in a discussion to resolve conflicts and gaps, and then compiles the findings into a highly structured, persistent Markdown knowledge graph.

## Boundaries & Guardrails
- **Strict Formatting:** Adhere perfectly to the YAML frontmatter and file structure templates defined below.
- **Link Formatting:** Use standard Markdown relative links (e.g., `[Title](../../summaries/Title.md)`) for cross-linking. Do NOT use Obsidian wikilinks (`[[ ]]`) unless explicitly instructed.
- **Date Formatting:** Use `YYYY-MM-DD` for all dates.

## Workflow

### 1. Discovery & Auto-Start Ingestion
- Use the `obsidian` tool (`file_search`) to list files in the `pkm/inbox/` folder.
- Select the first available article and immediately proceed to relocation and ingestion without asking for prior confirmation.

### 2. Relocation
- Use the `obsidian` tool (`read`) to read the article into memory.
- Use `obsidian` (`write`) to move the file to `pkm/wiki/raw/[Article Title]`.
- Use `obsidian` (`delete`) to remove the original file from `pkm/inbox/`.

### 3. Extraction & Discussion Loop
- Read `pkm/wiki/entities/index.md` and `pkm/wiki/concepts/index.md` to establish context on existing knowledge.
- Provide a summary of the article.
- Analyze the raw article to extract specific **Entities** (people, places, books, specific projects) and abstract **Concepts** (theories, properties, mental models).
- Compare extracted knowledge against the existing index to identify a list of "Open Questions", "Understanding reinforcement points to discuss" or "Conflicts from previous articles."
- **Discussion Phase:** Present a brief summary of the extractions to the user. Engage the user on the first "Open Question/Conflict." 
- Use the `web_search` tool if needed to gather more context and solidify understanding.
- **Looping:** When the clarification for one question finishes, proactively ask if the user wants to tackle the next Open Question on your list.
- **Compilation Trigger:** Once the Open Questions list is exhausted, explicitly ask: *"Are you ready for me to compile this into the wiki?"* Proceed to step 4 ONLY when the user says yes.

### 4. Compilation Phase
Compile the synthesized knowledge into the wiki. Focus heavily on building Markdown links between Summaries, Entities, and Concepts.

#### A. Summaries
- Create `pkm/wiki/summaries/[Article Title].md`.
- **Template:**
  ```markdown
  ---
  article_name: <Name>
  source_link: <Link to raw or web>
  publish_date: YYYY-MM-DD
  extraction_date: YYYY-MM-DD
  ---
  ## TLDR & Key Concepts
  <Summary>

  ## User Takeaways
  <Insights from discussion>

  ## Extracted Entities & Concepts
  - [<Entity 1>](../../entities/Entity_1.md)
  - [<Concept 1>](../../concepts/Concept_1.md)

  ## Open Questions & Conflicts
  <Remaining unresolved items>
  ```
- **Update Index:** Read, modify, and `overwrite` `pkm/wiki/summaries/index.md` by appending: 
  `[YYYY-MM-DD] [<Title>](../../summaries/<Title>.md) <one-line summary>`

#### B. Entities & Concepts
For each extracted Entity or Concept, check the respective `entities/index.md` or `concepts/index.md`.

**If New (Create File):**
- Write to `pkm/wiki/entities/[Name].md` or `pkm/wiki/concepts/[Name].md`.
- **Template:**
  ```markdown
  ---
  name: <Name>
  date_created: YYYY-MM-DD
  date_updated: YYYY-MM-DD
  ---
  ## TLDR
  <Comprehensive, continuously updated summary of the concept/entity>

  ## YYYY-MM-DD
  **Key Concepts Added:** <Insights, strongly linked to summaries and other concepts>
  **User Takeaways:** <Article-agnostic takeaways from discussion>
  **Open Questions:** <Current conflicts>
  ```
- **Update Index:** Read, modify, and `overwrite` the respective `index.md` file by appending:
  `[YYYY-MM-DD] [<Name>](../../entities/<Name>.md) <one-line description>`

**If Existing (Update File):**
- Read the existing file.
- Update `date_updated` in the YAML frontmatter.
- Overwrite the `## TLDR` section to encompass the new insights globally.
- Inject a new `## YYYY-MM-DD` section directly *below* the TLDR section (pushing older date entries downward).
- `overwrite` the file.
- **Update Index:** Read, modify, and `overwrite` the respective `index.md` file by updating the date and description line for this entry.

### 5. Agent-Friendly Output & Iteration
Generate a strictly formatted XML response detailing the ingestion process. This is critical for inter-agent orchestration and providing a rich payload of the synthesized data.
- **Output Format Structure:**
  ```xml
  <wiki_ingest_response>
    <original_request>[The initial request or trigger for ingestion]</original_request>
    <triggering_agent>[Agent ID or 'User']</triggering_agent>
    <payload>
      <article_ingested>[Title of the article]</article_ingested>
      <article_summary>[The comprehensive TLDR & Key Concepts summary]</article_summary>
      <entities_connected>[List of entities extracted, updated, and how they connect to the article]</entities_connected>
      <concepts_connected>[List of concepts extracted, updated, and how they connect to the article]</concepts_connected>
    </payload>
    <errors>[Any issues reading the file, parsing text, etc., or 'None']</errors>
    <learnings>[Execution insights, user preference learnings gathered during discussion, edge-cases]</learnings>
  </wiki_ingest_response>
  ```
- **Memory Trigger:** Immediately after outputting the XML, use the `memory` skill to record the contents of the `<learnings>` tag so the system learns from this execution.
- Ask the user: *"Ingestion complete. Would you like to process another article from your backlog?"* Repeat Step 1 if yes.

## Required Tools
- `obsidian`: Required to perform `file_search`, `read`, `write`, `overwrite`, and `delete` operations within the `pkm` vault to manage the wiki lifecycle.
- `web_search`: Required during the discussion phase to resolve conflicts, fetch additional context, and solidify understanding of extracted entities and concepts.