---
name: wiki_ingest
description: Systematically processes a reading backlog, extracts/discusses entities and concepts, and compiles a dense, markdown-linked LLM Wiki graph. Outputs agent-friendly XML.
---

## Overview
This skill implements the "LLM Wiki" pattern. It systematically reads articles from a backlog, extracts specific Entities and abstract Concepts, engages the user in a discussion to resolve conflicts and gaps, and then compiles the findings into a highly structured, persistent Markdown knowledge graph.

## Operating Modes
This skill can be executed in two modes depending on the triggering prompt:
1. **Interactive Mode (Default):** Engages the user in discussion and asks for explicit permission before compiling.
2. **Silent Mode:** (Triggered if the orchestrating agent specifies "Run in silent mode"). Bypasses the discussion and compilation triggers. It autonomously extracts, compiles, and outputs the final IPC XML without pausing for human input.

## Boundaries & Guardrails
- **Strict Formatting:** Adhere perfectly to the YAML frontmatter and file structure templates defined below.
- **Link Formatting:** Use standard Markdown relative links (e.g., `[Title](../../summaries/Title.md)`) for cross-linking. Do NOT use Obsidian wikilinks (`[[ ]]`) unless explicitly instructed.
- **Date Formatting:** Use `YYYY-MM-DD` for all dates.

## Workflow

### 1. Discovery & Confirmation
- Use the `obsidian` tool (`file_search`) to list files in the `pkm/inbox/` folder.
- **Interactive Mode:** Identify one article and ask the user: *"Would you like to focus on [Article Title]?"* If declined, select the next.
- **Silent Mode:** Select the requested article and proceed automatically.

### 2. Relocation
- Use the `obsidian` tool (`read`) to read the article into memory.
- Use `obsidian` (`write`) to move the file to `pkm/wiki/raw/[Article Title]`.
- Use `obsidian` (`delete`) to remove the original file from `pkm/inbox/`.

### 3. Extraction & Discussion Loop
- Read `pkm/wiki/entities/index.md` and `pkm/wiki/concepts/index.md` to establish context on existing knowledge.
- Analyze the raw article to extract specific **Entities** and abstract **Concepts**.
- Compare extracted knowledge against the existing index to identify "Open Questions" or "Conflicts."
- **Interactive Mode:** Present a brief summary. Engage the user on Open Questions. Use `web_search` if needed. Ask: *"Are you ready for me to compile this into the wiki?"* Proceed ONLY when the user says yes.
- **Silent Mode:** Automatically use your best judgment to resolve basic conflicts. Skip the user discussion and proceed directly to Phase 4.

### 4. Compilation Phase
Compile the synthesized knowledge into the wiki. Focus heavily on building Markdown links.

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
  <Insights from discussion (or AI synthesis if in Silent Mode)>

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
  **User Takeaways:** <Article-agnostic takeaways>
  **Open Questions:** <Current conflicts>
  ```
- **Update Index:** Append to the respective `index.md` file.

**If Existing (Update File):**
- Read the existing file. Update `date_updated`.
- Overwrite the `## TLDR` section to encompass the new insights globally.
- Inject a new `## YYYY-MM-DD` section directly *below* the TLDR section. `overwrite` the file.
- **Update Index:** Update the respective line in `index.md`.

### 5. Agent-Friendly Output (IPC Format)
Generate the XML response detailing the ingestion. This is critical for inter-agent orchestration.
```xml
<wiki_ingest_response>
  <original_request>[The initial request or trigger for ingestion]</original_request>
  <triggering_agent>[Agent ID or 'User']</triggering_agent>
  <payload>
    <article_ingested>[Title of the article]</article_ingested>
    <new_entities>[List of newly created entities]</new_entities>
    <new_concepts>[List of newly created concepts]</new_concepts>
    <updated_entities>[List of updated entities]</updated_entities>
    <updated_concepts>[List of updated concepts]</updated_concepts>
  </payload>
  <errors>[Any issues reading the file, parsing text, etc., or 'None']</errors>
  <learnings>[Execution insights, user preference learnings, edge-cases]</learnings>
</wiki_ingest_response>
```
**Memory Trigger:** Immediately after outputting the XML, use the `memory` skill to record the contents of the `<learnings>` tag.
- **Interactive Mode Only:** Ask the user: *"Ingestion complete. Would you like to process another article?"* Repeat Step 1 if yes.