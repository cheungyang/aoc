---
name: wiki_ingest
description: Systematically processes a reading backlog, extracts/discusses entities and concepts, and compiles a dense, markdown-linked LLM Wiki graph.
---

## Overview
This skill implements the "LLM Wiki" pattern. It systematically reads articles from a backlog, extracts specific Entities and abstract Concepts, engages the user in a discussion to resolve conflicts and gaps, and then compiles the findings into a highly structured, persistent Markdown knowledge graph.

## Boundaries & Guardrails
- **Strict Formatting:** Adhere perfectly to the YAML frontmatter and file structure templates defined below.
- **Link Formatting:** Use standard Markdown relative links (e.g., `[Title](../../summaries/Title.md)`) for cross-linking. Do NOT use Obsidian wikilinks (`[[ ]]`) unless explicitly instructed.
- **Date Formatting:** Use `YYYY-MM-DD` for all dates.
- **Strict Directory Boundary:** You MUST NOT create new top-level directories in the `pkm/wiki/` root. All output must strictly go into `summaries/`, `entities/`, `concepts/`, or `clusters/`.

## Workflow

### 1. Discovery & Auto-Start Ingestion
- Use the `filesystem` tool (`find` or `ls`) to list files in the `pkm/inbox/` folder.
- Select the first available article and immediately proceed to relocation and ingestion without asking for prior confirmation.

### 2. Relocation
- Use the `filesystem` tool (`read`) to read the article into memory.
- Use `filesystem` (`write`) to move the file to `pkm/wiki/raw/[Article Title]`.
- Use `filesystem` (`delete`) to remove the original file from `pkm/inbox/`.

### 3. Extraction & Discussion Loop
- Read `pkm/wiki/entities/index.md` and `pkm/wiki/concepts/index.md` to establish context on existing knowledge.
- Provide a summary of the article.
- Analyze the raw article to extract specific **Entities** (people, places, books, specific projects) and abstract **Concepts** (theories, properties, mental models).
- Compare extracted knowledge against the existing index to identify a list of "Open Questions", "Understanding reinforcement points to discuss" or "Conflicts from previous articles."
- **Clustering Opportunity Check:** Evaluate the growing knowledge base for "clusters". A clustering opportunity is strictly defined as:
  a) A topic worthy of further refinement (opportunity to deep-dive).
  b) Containing more than 3 existing related concepts/entities.
  c) Having a clear, overarching definition.
- **Discussion Phase:** Present a brief summary of the extractions to the user. Engage the user on the first "Open Question/Conflict." 
  - *HOLD POINT for Clusters:* If a clustering opportunity was identified, you MUST present the idea during this phase and explicitly ask: *"I see an opportunity to cluster these under [Name]. May I create a new cluster file?"* DO NOT create clusters without affirmative consent.
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

#### C. Clusters (If Approved)
If the user approved a cluster during the discussion phase:
**If New (Create File):**
- Write to `pkm/wiki/clusters/[Name].md`.
- **Template:**
  ```markdown
  ---
  name: <Name>
  date_created: YYYY-MM-DD
  date_updated: YYYY-MM-DD
  ---
  ## Definition
  <Brief summary defining the cluster>

  ## YYYY-MM-DD
  **Entities/Concepts Clustered:** <Links to the >3 entities/concepts>
  **Key Insights:** <Why this forms a cluster and takeaways>
  ```
**If Existing (Update File):**
- Read the existing cluster file.
- Update `date_updated` in the YAML frontmatter.
- Overwrite the `## Definition` if the understanding has expanded.
- Inject a new `## YYYY-MM-DD` section below the Definition with newly linked entities/concepts and insights.
- `overwrite` the file.

### 5. Recap & Iteration
- Reply in plain Markdown, never XML (your reply is shown in Discord): the article ingested and its TLDR, the entities and concepts extracted or updated and how they connect to it, any clusters created or updated, and any errors reading or parsing the file.
- **Memory Trigger:** End with a `<system_memory_log>` block recording execution insights, the user's preferences from the discussion, and edge cases.
- Ask the user: *"Ingestion complete. Would you like to process another article from your backlog?"* Repeat Step 1 if yes.

## Required Tools
- `filesystem`: Required to perform `find`, `read`, `write`, `overwrite`, and `delete` operations within the `pkm` vault to manage the wiki lifecycle.
- `web_search`: Required during the discussion phase to resolve conflicts, fetch additional context, and solidify understanding of extracted entities and concepts.