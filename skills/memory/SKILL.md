---
name: memory
description: MUST LOAD to record task completions, feedback, or context into daily logs, outputting only text confirmation.
---
## Overview
This skill guides the agent to capture important context, operational memory, and human feedback from conversations. This data is stored in a daily log to be processed later, ensuring the agent becomes smarter over time.

## When to Use (Triggers)
Do NOT evaluate or trigger this skill on every conversational turn. Only trigger this skill when:
1. An overall task or workflow has reached completion.
2. Explicit/Implicit feedback from the human is clearly detected.
3. Evergreen personal context about the human is revealed.

## Boundaries & Guardrails
- **Passive Collection**: NEVER explicitly ask the user for personal information, preferences, or feedback just to fulfill this skill. Infer it naturally from the conversation.
- **Evergreen Only**: Only record permanent or long-standing context. Ignore temporary states.
- **Formatting**: Provide only a brief natural language confirmation; NEVER output XML.

## Extraction Categories
When triggered, identify and extract information into the following categories:

### 1. Memory (Task Log)
- What was the agent asked to perform?
- Was the operation successful?
- Were there any errors or issues? What were they?
- What decisions were made? Are there any pending decisions?
- Was the human satisfied?

### 2. Feedback (Course Correction)
- Explicit Feedback: Direct instructions on behavior.
- Implicit Feedback: Indirect corrections.

### 3. Context (User Persona)
- **Factual**: Age, occupation, location, etc.
- **Evergreen Relationships**: Spouse, kids, pets.
- **Personal Preferences**: Habits, likes/dislikes.

## Execution Workflow

### Step 1: Format the Entry
Format the extracted information cleanly. Use the current timestamp and categorize the data clearly. Ensure each entry is written on its own distinct line.
*Example Format:*
`- [HH:MM:SS] [MEMORY] Task: Wrote python script. Status: Success. Decisions: Used pandas over csv module.`
`- [HH:MM:SS] [CONTEXT] Evergreen: User has a golden retriever named Max.`

### Step 2: Store the Data
Use the `obsidian` tool's `append` (or `write` if the file doesn't exist) action to save the formatted entry. 
- **Vault**: `pkm`
- **Path**: `agents/<agent_id>/memory_logs/YYYY-MM-DD.md` (Replace with current date).
- **Formatting Note**: Make sure to include a newline character at the end of your entry so that the next log starts on a fresh line.

### Step 3: Conversational Confirmation (Text Only)
Do NOT output any XML or IPC payloads (like `<memory_response>`) in your response. Because memory changes are tracked externally by the file system, you must ONLY provide a brief, natural language confirmation to the user that the log/feedback was recorded.

**User Feedback Acknowledgement**: If (and ONLY if) you recorded a **Feedback** item, you must explicitly acknowledge this in conversational text, proving to the user their feedback is integrated.

## Required Tools
- `obsidian`: Required to use the `append` or `write` action on `agents/<agent_id>/memory_logs/YYYY-MM-DD.md` within the `pkm` vault. Requires `agent-scoped` permissions.