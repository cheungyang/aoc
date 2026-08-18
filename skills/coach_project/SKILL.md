---
name: coach_project
description: Analyzes project metadata and tasks, connects knowledge via wiki_query, and maintains a strategic coaching dossier to facilitate proactive user coaching sessions.
---

## Overview
This skill acts as the agent's internal preparation pipeline for project coaching. It pulls structured metadata and tasks from the DB, checks the wiki for related concepts, and writes an internal "Coaching Dossier" for the project. This dossier serves as the agent's memory and strategy guide for proactive coaching sessions.

## Boundaries & Guardrails
- **Zero Filesystem Discovery:** You MUST NOT use `filesystem` search to find tasks or project states. Rely purely on the JSON returned by `task_query` and `project_query`.
- **Anti-Fluff Guardrail (Task Appending):** You have permission to append new tasks directly to the user's raw files in `pkm/vault/projects/`. However, you MUST NEVER write generic, fluffy tasks (e.g., "Research topic", "Think about next steps"). Tasks must be hyper-specific and verifiable. You MUST explicitly ask the user for permission before writing tasks to their vault.
- **Dossier Isolation:** All coaching dossiers MUST be saved strictly to `pkm/wiki/project-coaching/`. Do not pollute the main `projects` folder.

## Workflow

### Phase 1: DB-Driven Ingest
1. Use `project_query` (`action="get"`) to retrieve the structured JSON metadata for the target project.
2. Extract the project's `status`, `priority`, `commitment_year`, `category`, and the `alias` tag (e.g., `#p/my-project`) from the JSON payload.
3. Use `task_query` (`action="search"`) to retrieve tasks associated with this project. Use the `tags` array parameter to search for the project's alias (e.g., `tags=["p/<alias>"]`).

### Phase 2: Contextualization & Velocity
4. Analyze the returned metadata and tasks. Identify key Entities and Concepts critical to the project's success.
5. Trigger the `wiki_query` skill to ask: *"What do we already know about [Entity/Concept] in relation to this project domain?"* to ground your coaching in the user's existing knowledge graph.
6. Evaluate task velocity by comparing completed vs. uncompleted tasks from your DB pull to identify bottlenecks.

### Phase 3: The Coaching Dossier
7. Use the `filesystem` tool (`write` or `overwrite` action) to create/update your strategy file at `pkm/wiki/project-coaching/<project_name>.md`.
8. Use the following strict template for your dossier:
   ```markdown
   ---
   project_name: <Name>
   last_coached: YYYY-MM-DD
   ---
   **Status:** <status> | **Priority:** <priority> | **Year:** <commitment_year> | **Category:** <category>
   
   ## Coaching Strategy
   <Your internal notes on how to push the user forward. What are the blockers? What hard questions do you need to ask them next?>

   ## Knowledge Graph Context
   <Markdown links and brief syntheses of related concepts you found via wiki_query>
   ```

### Phase 4: Proactive Outreach (Agent Output)
9. Output a conversational message to the user initiating the coaching session based on your updated dossier.
10. **The Final Output:** Do NOT output an XML block for this skill. End your execution by speaking directly to the user, asking them your strategic questions to unblock the project.

## Required Tools
- `project_query` & `task_query`: Required for instant, structured data ingestion.
- `filesystem`: Required to write your dossier to `pkm/wiki/project-coaching/` and to append user-approved tasks to `pkm/vault/projects/`.
- `git`: Required to ensure the vault is synced if necessary.