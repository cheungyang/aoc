---
name: memory
description: MUST LOAD this skill when an overall task concludes, or when human feedback or personal context is detected, to record memories and continuous learning data.
---
## Overview
This skill guides the agent to capture important context, operational memory, and human feedback from conversations. This data is stored in a daily log to be processed later, ensuring the agent becomes smarter and more personalized over time.

## When to Use (Triggers)
Do NOT evaluate or trigger this skill on every conversational turn. Only trigger this skill when:
1. An overall task or workflow has reached completion.
2. Explicit/Implicit feedback from the human is clearly detected.
3. Evergreen personal context about the human is revealed.

## Boundaries & Guardrails
- **Passive Collection**: NEVER explicitly ask the user for personal information, preferences, or feedback just to fulfill this skill. Infer it naturally from the conversation.
- **Evergreen Only**: Only record permanent or long-standing context. Ignore temporary states (e.g., "I went to the store today" is ignored; "I have a dog" is recorded).
- **Silent Operation**: Information gathering must not disrupt or alter the primary task execution.

## Extraction Categories
When triggered, identify and extract information into the following categories:

### 1. Memory (Task Log)
- What was the agent asked to perform?
- Was the operation successful?
- Were there any errors or issues? What were they?
- What decisions were made? Are there any pending decisions?
- Was the human satisfied? (e.g., did they ask clarifying questions or express frustration?)

### 2. Feedback (Course Correction)
- Explicit Feedback: Direct instructions on behavior (e.g., "feedback to you: always use markdown tables").
- Implicit Feedback: Indirect corrections (e.g., "you should have searched the web first").

### 3. Context (User Persona)
- **Factual**: Age, occupation, location, etc.
- **Evergreen Relationships**: Spouse, kids, pets (Avoid temporary relationship context).
- **Personal Preferences**: Habits, likes/dislikes (e.g., prefers walking over driving, morning person).

## Execution Workflow

### Step 1: Format the Entry
Format the extracted information cleanly. Use the current timestamp and categorize the data clearly. Ensure each entry is written on its own distinct line.
*Example Format:*
`- [HH:MM:SS] [MEMORY] Task: Wrote python script. Status: Success. Decisions: Used pandas over csv module.`
`- [HH:MM:SS] [CONTEXT] Evergreen: User has a golden retriever named Max.`

### Step 2: Store the Data
Use the `obsidian` tool's `append` action to write the formatted entry. 
- **Path**: `agents/<agent_id>/memory_logs/YYYY-MM-DD.md` (Replace with current date).
- **Formatting Note**: Make sure to include a newline character at the end of your entry so that the next log starts on a fresh line.
- **Note**: The `append` action will keep existing entries and add the new ones to the bottom.

### Step 3: Acknowledge Feedback (Conditional)
- If (and ONLY if) you recorded a **Feedback** item, you must explicitly acknowledge this in your response to the user.
- Briefly summarize what you recorded and state that you will remember it, proving to the user that their feedback is taken seriously and integrated into your learning.

## Required Tools
- `obsidian`: Required to use the `append` action on `agents/<agent_id>/memory_logs/YYYY-MM-DD.md` within the `pkm-oc` vault. Requires `agent-scoped` permissions.