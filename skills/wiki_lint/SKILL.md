---
name: wiki_lint
description: Automates wiki maintenance: fixes links, creates stubs, flags contradictions, logs modifications, and outputs IPC XML.
---

## Overview
This skill acts as the automated gardener for the LLM Wiki. It ensures the knowledge graph remains healthy by autonomously fixing structural issues (broken links, index mismatches), creating placeholder stubs for frequently mentioned but undocumented concepts, and flagging semantic contradictions for human review. It records actions in a daily log and outputs a standardized IPC XML response.

## Boundaries & Guardrails
- **Auto-Fix Authority:** You MAY automatically fix minor markdown link typos, formatting errors, and missing `index.md` entries without asking.
- **Stub Creation:** You MAY autonomously create stub pages for highly referenced missing concepts.
- **Semantic Edits:** You MUST NOT automatically resolve semantic contradictions or delete established knowledge. Present these to the user for resolution.
- **Logging Constraint:** EVERY modification or flagged opportunity must be recorded in the daily lint report.

## Workflow

### 1. Scope & Discovery
- If the user does not specify a scope, default to a **Recent Updates Audit**. Focus your reads on files recently added or modified, and their corresponding `index.md` files in `/concepts`, `/entities`, `/summaries`, and `/syntheses`.
- Use the `obsidian` tool (`file_search` and `read`) to gather the necessary context.

### 2. The Audit Phase
Perform a dual-layer audit on the scoped files:

**A. Structural Audit:**
- **Broken Links:** Find markdown links pointing to non-existent files.
- **Orphans:** Find files that exist but are not documented in their directory's `index.md`.
- **Formatting:** Check for valid YAML frontmatter and the required `## YYYY-MM-DD` section structure.

**B. Semantic Audit:**
- **Contradictions:** Look for logical inconsistencies between linked files (e.g., a summary claims X, but the linked concept claims Y).
- **Missing Concepts:** Identify terms or entities that are frequently mentioned across multiple files but lack their own dedicated page.

### 3. Autonomous Fixes & Stub Creation
Execute safe repairs immediately:
- **Auto-Fixes:** Use `obsidian` (`overwrite`) to correct link typos, fix frontmatter, or append missing lines to `index.md`.
- **Stub Creation:** 
  - For missing concepts/entities, use `write` to create a new file.
  - *Template:* Include standard frontmatter and a body of: `## TLDR: [STUB] Requires expansion.`
  - *Index Entry:* Append to the respective `index.md` with a `[STUB]` tag in the description: 
    `[YYYY-MM-DD] [<Name>](../../concepts/<Name>.md) [STUB] <Brief context from where it was mentioned>`

### 4. The Linting Log
You must document every action and discovery in a daily log file.
- **Path:** `pkm/wiki/lint_reports/YYYY-MM-DD.md` (Replace with current date).
- **Format:** Each entry must be on a single new line:
  `[<ACTION>] <file_path>: <description>`
- **Valid Actions:** `[AUTO-FIX]`, `[STUB-CREATED]`, `[CONFLICT-FLAGGED]`, `[OPPORTUNITY-LOGGED]`, `[EDIT]`.
- Use the `obsidian` tool (`write` if the file doesn't exist, or `append` if it does) to store these logs.

### 5. Interactive Resolution & Agent-Friendly Output
After executing auto-fixes and logging:
- Present a conversational report to the user summarizing `[AUTO-FIX]` and `[STUB-CREATED]` actions.
- Present any **Contradictions/Conflicts** and ask the user how to resolve them. Use `obsidian` to `overwrite` files based on user guidance and log the `[EDIT]`.
- **Final IPC Output:** Once all interactive resolution is complete, output the final state using the strict XML structure below to ensure readability for routing agents.
  ```xml
  <wiki_lint_response>
    <original_request>[The scope or request given to the linter]</original_request>
    <triggering_agent>[Agent ID or 'User']</triggering_agent>
    <payload>
      <auto_fixes_applied>[Number/summary of structural fixes made]</auto_fixes_applied>
      <stubs_created>[List of missing concept stubs created]</stubs_created>
      <conflicts_resolved>[Summary of user-assisted semantic conflict resolutions]</conflicts_resolved>
    </payload>
    <errors>[Any files that couldn't be accessed/edited, or 'None']</errors>
    <learnings>[Trends in broken links, common structural errors noted for future prevention]</learnings>
  </wiki_lint_response>
  ```
- **Memory Trigger:** Immediately after outputting the XML, use the `memory` skill to record the contents of the `<learnings>` tag so the system learns to prevent these structural issues.

## Required Tools
- `obsidian`: Required to `file_search`, `read`, `write`, `overwrite`, and `append` files within the `pkm` vault to audit the wiki, enact repairs, create stubs, and maintain the daily lint report.