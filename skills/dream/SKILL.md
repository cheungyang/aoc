---
name: dream
description: Skill for synthesizing daily memory logs into token-efficient context files, returning the rewritten files as IPC XML.
---
## Overview
The dream skill is a daily consolidation routine. The dream standup hands you your unprocessed daily memory logs and the current contents of your permanent reference files (`MEMORY.md`, `FEEDBACK.md`, `CONTEXT.md`) inline, inside a `<dream_input>` block. You extract the highly relevant learnings, feedback and context from the logs, merge them into the reference files, and return the complete rewritten files as XML. The standup script validates your reply, writes the files and deletes the processed logs; you never touch the filesystem. Because these reference files are injected into your system prompt on every interaction, they must remain fiercely concise and actionable.

## When to Use
This skill is triggered EXCLUSIVELY by the dream standup. Do not trigger this organically during standard conversations.

## Boundaries & Guardrails
- **No Tools**: Everything you need is in `<dream_input>`. Do not call `filesystem` or any other tool, and do not emit a `<system_memory_log>` — a dream that writes a log creates tomorrow's dream.
- **Hard Size Budget**: Each of `MEMORY.md`, `FEEDBACK.md` and `CONTEXT.md` MUST stay at or under **2000 characters**. These three files are injected into the system prompt on *every single turn*, so a character saved here is saved on every future interaction. If a file would be over budget, you MUST keep cutting — merge the weakest-value entries or drop them entirely — until it fits. Never return a file over budget.
- **Expire Time-Bound Facts**: Every dream, re-read the *existing* entries and delete those that no longer apply. This is not optional and is the main reason these files grow. Retire an entry when:
  - it describes a dated event that has passed (a trip, an appointment, a deadline);
  - it is phrased as a transient state ("Recent…", "ongoing as of…", "currently…") and the date has moved on;
  - it records a one-time migration or correction that is now simply how things are;
  - a newer entry supersedes it — keep the newer one only, never both.
- **Deduplicate Across All Three Files**: A fact belongs in exactly one file. Keep only the best-placed copy.
- **Merge, Do Not Accumulate**: When a new entry concerns a subject already covered, rewrite the existing line to incorporate it and extend its `(Ref: ...)` dates. Two entries on one subject is a defect.
- **Conciseness is Critical**: The long-term files must be straight to the facts. Never include conversational filler.
- **Synthesis, Not Appending**: Do NOT simply append new facts. Merge new information into the existing content, resolve redundancies or conflicts (newer info overrides older info), and return the whole file.
- **Actionable Extractions**: Only extract data that improves future decision-making or personalization. Ignore routine operations.
- **Preserve Earned Behaviour**: Behavioural corrections from the user are the highest-value content here. When cutting to fit the budget, drop stale context and expired events first; drop a behavioural rule only if it has genuinely been superseded.
- **Formatting**: The reply MUST be only the XML below. YOU MUST NOT OUTPUT CONVERSATIONAL TEXT outside it.

## Workflow

### 1. Read the Input
`<dream_input>` holds `<current_files>` (one `<file name="...">` per reference file; empty if the file does not exist yet) and `<memory_logs>` (one `<log name="YYYY-MM-DD.md">` per daily log).

### 2. Extract & Synthesize
Carefully parse the logs for items worthy of long-term retention:
- **Memory (Learnings & Precedents)**: Do NOT log routine successes or standard task completions. Only extract *what/how* made a task successful, *why* a failure occurred, or specific decisions made that serve as future precedents.
- **Feedback (Behavioral Rules)**: Translate user feedback into direct, concise behavioral commands.
- **Context (Evergreen Persona)**: Consolidate persistent user context and preferences. Ignore temporary states.

Merge them with the current files, applying every guardrail above.

### 3. Reply (IPC Format)
- **Dreamed**: Anything changed — including expiring or merging existing entries. Each of `<memory_md>`, `<feedback_md>` and `<context_md>` MUST hold the **complete new contents** of that file, not a diff or a summary. An unchanged file is returned unchanged. All three tags are required; a reply missing one is rejected and nothing is written.
- **No new memories**: Nothing in the logs is worth keeping and no existing entry needs to change. Omit the three file tags. The logs are still deleted.

```xml
<dream_response>
  <status>Dreamed</status>
  <memory_md>...complete MEMORY.md...</memory_md>
  <feedback_md>...complete FEEDBACK.md...</feedback_md>
  <context_md>...complete CONTEXT.md...</context_md>
  <errors>None</errors>
  <learnings>One concise line to show in the daily standup.</learnings>
</dream_response>
```

Write real content, never the bracketed descriptions of a template: a file tag whose whole content is `[...]` is rejected as an echoed placeholder.