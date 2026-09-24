---
name: wiki_lint
description: Interactively processes the pending_lint.json queue to resolve semantic duplicates and prune stale stubs.
---

## Overview
This skill operates as the interactive Editor-in-Chief for the LLM Wiki. It relies on a deterministic background script (`wiki_scanner.py`) to generate a queue of maintenance tasks (stored in `pkm/wiki/pending_lint.json`). When invoked, the agent reads this queue and walks the user through each flagged issue one by one, executing the user's decisions to merge, prune, or expand knowledge.

## Boundaries & Guardrails
- **No Autonomous Destruction:** You MUST NEVER autonomously delete, merge, or rewrite knowledge without the user's explicit, item-by-item approval.
- **One-by-One Pacing:** You MUST present only ONE queue item at a time. Do not overwhelm the user with a massive list.
- **Source of Truth:** Markdown files in `pkm/` are the absolute source of truth.
- **Every Removal Is Recorded:** An item leaves `review_queue` only by the user's decision, or by your own dismissal of a clear false positive (see 2A). Every dismissal is written to `pkm/wiki/lint_state.json` (Step 3) — never clear the queue without recording why. If the user wants to stop, leave the remaining items in place.

## Workflow

### 1. Queue Ingestion
- Use the `filesystem` tool (`read`) to read `pkm/wiki/pending_lint.json`. It has one key, `review_queue`: a list of items shaped `{"type": "duplicate_candidate" | "stale_stub", "files": ["wiki/<folder>/<note>.md", ...]}`. Paths are relative to `pkm/`. Duplicate candidates always pair two notes from the same folder.
- If the file does not exist or the `review_queue` array is empty, output a brief message stating: *"The wiki is clean! No pending lint items in the queue."* and terminate the skill.

### 2. The Interactive Loop (One Item at a Time)
Pick the first unresolved item in the `review_queue` and evaluate its `type`:

**A. If `type` is `duplicate_candidate` (Semantic Overlap):**
- Use `vault_search` or `filesystem` (`read`) to fetch the content of the flagged files.
- **Clear false positive:** if the two notes are plainly distinct concepts that only share vocabulary (e.g. `Ayla` vs `Kotlin`), don't ask: dismiss it yourself (Step 3, with `"by": "wiki-gardener"`), mention it in one line, and move on. When in doubt, ask.
- **Present to User:** Briefly summarize both files and highlight the overlap. Ask the user how to resolve it.
  - *Example:* "I found a 95% similarity between `Multi-Agent.md` and `Agent_Routing.md`. Would you like me to merge them, keep one, or keep both as distinct concepts?"
- **Execute:** Based on the user's answer, use `filesystem` to `overwrite` the target file with the merged text and `delete` the redundant file.

**B. If `type` is `stale_stub` (Prune or Expand):**
- Use `filesystem` (`read`) to check the stub's current content.
- **Present to User:** "The stub `[STUB] Concept` hasn't been edited in 6 months. Should I delete it, or would you like me to use `vault_search` to gather context and draft a full page for it right now?"
- **Execute:** Either `delete` the file or execute `vault_search` to synthesize a full page and `overwrite` the stub.

### 3. State Management (Queue Update)
- Immediately after you execute the user's decision for an item, use `filesystem` to `read` `pkm/wiki/pending_lint.json`.
- Remove the resolved item from the `review_queue` array.
- Use `filesystem` (`overwrite`) to save the updated JSON queue back to disk.
- **If the user chose to keep the files as they are** (e.g. "keep both, they're distinct"), record the dismissal so the nightly scanner doesn't queue it again: `read` `pkm/wiki/lint_state.json` (treat a missing file as `{}`), append `{"type": "<item type>", "files": [<the item's files>], "date": "YYYY-MM-DD", "by": "user" | "wiki-gardener"}` to its `dismissed` list, keep every other key unchanged, and `overwrite` it. The scanner re-queues the item by itself if either file is edited later.
- **Hold Point:** Ask the user if they would like to proceed to the next item in the queue, or stop for now. If they choose to continue, repeat Step 2.

### 4. Logging & Memory
- If the user decides to stop, or the queue is fully cleared, use the `memory` skill to record the session. 
- *Example Memory:* `[MEMORY] Task: Wiki Lint Session. Status: Success. Decisions: Merged 2 duplicate concepts, deleted 1 stale stub.`

## Required Tools
- `filesystem`: Required to read/update the JSON queue, read file contents, overwrite merged files, and delete pruned files. Requires access to `pkm/wiki/`.
- `vault_search`: Required to pull additional context if the user asks you to expand a stale stub into a full page.