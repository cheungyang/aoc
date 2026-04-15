---
name: dream
description: Skill for synthesizing daily memory logs into token-efficient context files, outputting IPC XML.
---
## Overview
The dream skill is a daily consolidation routine. It reads active daily memory logs, extracts highly relevant learnings, feedbacks, and context, and synthesizes them into permanent reference files (`MEMORY.md`, `FEEDBACK.md`, `CONTEXT.md`). It then archives the processed logs and outputs an IPC XML block. Because these root files are injected into the agent's system prompt on every interaction, they must remain fiercely concise and actionable.

## When to Use
This skill is triggered EXCLUSIVELY by a cron schedule or explicit system prompt. Do not trigger this organically during standard conversations.

## Boundaries & Guardrails
- **Conciseness is Critical**: The long-term files must be straight to the facts. Never include conversational filler.
- **Synthesis, Not Appending**: Do NOT simply append new facts to the long-term files. You must read the existing files, merge new information, resolve redundancies or conflicts (newer info overrides older info), and then overwrite the file.
- **Actionable Extractions**: Only extract data that improves future decision-making or personalization. Ignore routine operations.
- **Formatting**: The final output MUST strictly adhere to the requested IPC XML structure.

## Workflow

### 1. The Backup Phase (Failsafe)
Before altering long-term memory, back up the current state.
- Use the `obsidian` tool to read the current contents of `agents/<agent_id>/MEMORY.md`, `FEEDBACK.md`, and `CONTEXT.md` (if they exist) from the `pkm` vault.
- Use the `obsidian` tool's `write` action to save exact copies into the archive directory with today's date: 
  - `agents/<agent_id>/memory_archive/MEMORY_YYYY-MM-DD_bak.md`
  - `agents/<agent_id>/memory_archive/FEEDBACK_YYYY-MM-DD_bak.md`
  - `agents/<agent_id>/memory_archive/CONTEXT_YYYY-MM-DD_bak.md`

### 2. The Discovery Phase
- Use the `obsidian` tool's `file_search` action on the `agents/<agent_id>/memory_logs/` path to find active log files.
- Identify any daily log files present in this directory. If the directory is empty, the dream skill is complete (skip to Step 5).

### 3. Processing & Consolidation Phase
For every log file discovered in Step 2, process it one by one:

**A. Read the Log:**
Read the contents of the `YYYY-MM-DD.md` log file using the `obsidian` tool.

**B. Extract & Synthesize:**
Carefully parse the log for items worthy of long-term retention:
- **Memory (Learnings & Precedents)**: Do NOT log routine successes or standard task completions. Only extract *what/how* made a task successful, *why* a failure occurred, or specific decisions made that serve as future precedents. If there are no new learnings, ignore it.
- **Feedback (Behavioral Rules)**: Translate user feedback into direct, concise behavioral commands. (e.g., Instead of "User was annoyed I didn't search," write: "Always search the web before answering factual questions").
- **Context (Evergreen Persona)**: Consolidate persistent user context and preferences. Ignore temporary states.

**C. Resolve & Overwrite:**
- Merge these newly extracted insights with the data currently inside the root `MEMORY.md`, `FEEDBACK.md`, and `CONTEXT.md`. 
- If new information contradicts old information, the newer log takes precedence. 
- Overwrite the root files with the newly synthesized, optimized text using the `obsidian` tool.

### 4. The Archiving Phase
Once a log file has been fully processed and its contents synthesized into the root files:
- Use the `obsidian` tool's `append` action to move the raw log file to the archive: `agents/<agent_id>/memory_archive/YYYY-MM-DD.md`. Be sure to prepend the appended text with a markdown separator (e.g., `\n---\n`) to visually distinguish multiple appended entries within the same day.
- Use the `obsidian` tool's `delete` action to completely remove the original active log file `memory_logs/YYYY-MM-DD.md` (effectively deleting the processed data from the active queue).

### 5. Agent-Friendly Output & Memory (IPC Format)
Finalize the execution using the strict XML structure below to ensure readability for routing and monitoring agents.
```xml
<dream_response>
  <original_request>[The trigger for the dream routine]</original_request>
  <triggering_agent>[Agent ID, 'System Cron', or 'User']</triggering_agent>
  <payload>
    <logs_processed>[Number of daily log files read and archived]</logs_processed>
    <files_updated>
      - pkm/agents/<agent_id>/MEMORY.md
      - pkm/agents/<agent_id>/CONTEXT.md
      - pkm/agents/<agent_id>/FEEDBACK.md
    </files_updated>
  </payload>
  <errors>[Any file reading, backup, or archiving errors, or 'None']</errors>
  <learnings>[High-level meta-insights about the agent's recent activity or recurring feedback trends noticed during synthesis]</learnings>
</dream_response>
```
**Memory Trigger**: Immediately after outputting the XML, use the `memory` skill to record the contents of the `<learnings>` tag so the system learns from its own synthesis process.

## Required Tools
- `obsidian`: Required to perform `file_search`, `read`, `write`, `overwrite`, `append`, and `delete` actions on memory logs and context files within the `pkm` vault. Requires `agent-scoped` permissions.