---
name: planning
description: Skill for educating the agent to reason, breakdown tasks, plan, communicate, and execute.
---
## Overview
This skill guides the agent on how to approach complex jobs by reasoning about them, breaking them down into manageable tasks, creating a plan, communicating with the human, and executing the tasks systematically.

## Workflow

### 1. Reason and Breakdown
- Assess the assigned job or goal carefully.
- Reason about the requirements, dependencies, and potential challenges.
- Break down the problem into a list of discrete, actionable tasks.

### 2. Understand and Plan
- For each task, determine how it will be executed (what tools or steps are needed).
- Ensure the order of tasks makes sense (handle dependencies).
- Formulate a clear plan of action.

### 3. Communicate Plan
- Present the broken-down plan to the human user.
- Explain the rationale briefly.
- Await feedback or approval if necessary, or simply inform the user of the plan before proceeding.

### 4. Execute and Combine
- Execute each task one by one.
- Track progress and handle any failures or adjustments needed.
- Combine the outcomes of individual tasks to produce the final output or solve the overall problem.
- Present the combined result to the human.

## Required Tools
- This is a cognitive skill and does not require specific external tools, but it leverages the agent's existing toolset for execution of the individual tasks. The agent should use project-specific tools as needed for each step.
