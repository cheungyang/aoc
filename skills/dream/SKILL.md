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
- **Formatting**: The final output MUST strictly adhere to the requested IPC XML structure. YOU MUST NOT OUTPUT CONVERSATIONAL TEXT outside the XML payload.
- **Strict Tool Usage**: You MUST strictly use the `filesystem` tool for all file and directory actions (`read`, `write`, `append`, `delete`).

## Workflow

### 1. The Backup Phase (Failsafe)
Before altering long-term memory, back up the current state.
- Use the `filesystem` tool to read the current contents of `pkm/agents/<agent_id>/MEMORY.md`, `FEEDBACK.md`, and `CONTEXT.md` (if they exist).
- Use the `filesystem` tool's `write` action to save exact copies into the archive directory with today's date: 
  - `agents/<agent_id>/memory_archive/MEMORY_YYYY-MM-DD_bak.md`
  - `agents/<agent_id>/memory_archive/FEEDBACK_YYYY-MM-DD_bak.md`
  - `agents/<agent_id>/memory_archive/CONTEXT_YYYY-MM-DD_bak.md`

### 2. The Discovery Phase
- Use the `filesystem` tool's `ls` or `find` action on the `pkm/agents/<agent_id>/memory_logs/` path to find active log files.
- Identify any daily log files present in this directory. If the directory is empty, the dream skill is complete. Proceed immediately to Step 5 (Output) and state "No new memories".

### 3. Processing & Consolidation Phase
For every log file discovered in Step 2, process it one by one:

**A. Read the Log:**
Read the contents of the `YYYY-MM-DD.md` log file using the `filesystem` tool.

**B. Extract & Synthesize:**
Carefully parse the log for items worthy of long-term retention:
- **Memory (Learnings & Precedents)**: Do NOT log routine successes or standard task completions. Only extract *what/how* made a task successful, *why* a failure occurred, or specific decisions made that serve as future precedents. If there are no new learnings, ignore it.
- **Feedback (Behavioral Rules)**: Translate user feedback into direct, concise behavioral commands.
- **Context (Evergreen Persona)**: Consolidate persistent user context and preferences. Ignore temporary states.

**C. Resolve & Overwrite:**
- Merge these newly extracted insights with the data currently inside the root `MEMORY.md`, `FEEDBACK.md`, and `CONTEXT.md`. 
- Overwrite the root files with the newly synthesized, optimized text using the `filesystem` tool.

### 4. The Archiving Phase
Once a log file has been fully processed and its contents synthesized into the root files:
- Use the `filesystem` tool's `append` action to move the raw log file to the archive. Be sure to prepend the appended text with a markdown separator (e.g., `\n---\n`) to visually distinguish multiple appended entries within the same day.
  - **Vault**: `pkm`
  - **Path**: `agents/<agent_id>/memory_archive/YYYY-MM-DD.md` 
- Use the `filesystem` tool's `delete` action to completely remove the original active log file `pkm/agents/<agent_id>/memory_logs/YYYY-MM-DD.md`.

### 5. Agent-Friendly Output & Memory (IPC Format)
Finalize the execution using the strict XML structure below to ensure readability for routing and monitoring agents. You must NOT include conversational text; only output the XML block.
```xml
<dream_response>
  <original_request>[The trigger for the dream routine]</original_request>
  <triggering_agent>[Agent ID, 'System Cron', or 'User']</triggering_agent>
  <payload>
    <status>[Strictly either 'Dreamed' or 'No new memories']</status>
    <logs_processed>[Number of daily log files read and archived]</logs_processed>
    <files_updated>
      - pkm/agents/<agent_id>/MEMORY.md
      - pkm/agents/<agent_id>/CONTEXT.md
      - pkm/agents/<agent_id>/FEEDBACK.md
    </files_updated>
  </payload>
  <errors>[Any file reading, backup, or archiving errors, or 'None']</errors>
  <learnings>[Concise meta-insight or extraction that should be shown in the daily standup]</learnings>
</dream_response>
```
**Memory Trigger**: Immediately after outputting the XML, use the `memory` skill to record the contents of the `<learnings>` tag so the system learns from its own synthesis process.