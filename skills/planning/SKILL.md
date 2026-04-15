---
name: planning
description: MUST LOAD this skill if the task benefits from laying out an execution plan, outputting standard IPC XML upon completion.
---
## Overview
This skill provides a structured workflow to help agents break down complex problems into smaller, actionable pieces, significantly increasing the success rate of multi-step tasks. It concludes its lifecycle by outputting a standard IPC XML block detailing the executed plan.

## When to Use
Use this skill when a task is complex enough that it requires multiple tool calls, multiple skills, or significant step-by-step reasoning before execution. 

## Boundaries & Guardrails
- **Phase Limit**: Maximum of 3 phases per plan.
- **Task Limit**: Maximum of 5 tasks per phase.
- **Tool Call Limit**: Maximum of 20 tool calls across the entire execution.
- **Strict Approval**: NEVER begin execution without explicit user approval of the initial or revised plan.
- **Formatting**: The final output MUST strictly adhere to the requested IPC XML structure.

## Workflow

### 1. Assess & Modularize
- Determine if the task requires planning.
- Reason about ways to modularize the problem into distinct, logical phases (e.g., Information Gathering Phase, Analysis Phase, Output Rendering Phase). 

### 2. Task Breakdown
- For each phase, break down the work into individually executable tasks.
- Each task must have a specific output or goal in mind.
- Output this breakdown as a markdown to-do list (`- [ ] Task`).

### 3. User Confirmation (Hold Point)
- Present the complete markdown task list to the user.
- **WAIT** for the user to review and explicitly confirm the plan before loading any tools or taking execution actions.

### 4. Sequential Execution & Status Updates
- Once approved, execute each bullet point sequentially.
- Load required skills or tools necessary to complete the specific task.
- **Token Efficiency**: Do NOT re-print the entire task list after each step. Instead, output a concise status update upon completing a task (e.g., `*Task 1 Complete: Starting Task 2...*`).

### 5. Adaptability & Course Correction
- If a task fails or if new information changes the situational requirements, **PAUSE execution**.
- Revise the remaining plan based on the newly discovered context.
- Present the revised plan to the user and **ask for explicit confirmation** before proceeding.

### 6. Agent-Friendly Output & Memory (IPC Format)
- When all tasks are successfully completed, compile the findings and finalize the execution using the strict XML structure below. This format ensures perfect readability for routing agents.
```xml
<planning_response>
  <original_request>[The user's original complex task request]</original_request>
  <triggering_agent>[Agent ID or 'User']</triggering_agent>
  <payload>
    <executed_plan>
      [A clean markdown list of the phases and tasks completed]
    </executed_plan>
    <final_output>
      [The ultimate deliverable or answer synthesized from the completed tasks]
    </final_output>
  </payload>
  <errors>[Any critical failures encountered during execution that required a plan revision, or 'None']</errors>
  <learnings>[Execution insights on what steps were most efficient, what failed, or how to better modularize similar tasks in the future]</learnings>
</planning_response>
```
**Memory Trigger**: Immediately after outputting the XML, use the `memory` skill to record the contents of the `<learnings>` tag so the system learns from this execution.

## Required Tools
- None. This skill relies entirely on reasoning and conversational generation. The execution of the planned tasks will rely on the native tools granted to the agent in their `agent.json`.