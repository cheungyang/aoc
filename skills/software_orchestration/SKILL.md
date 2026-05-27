---
name: software_orchestration
description: Deterministically orchestrates the software development lifecycle by routing tasks between agents based on live system state.
---
## Overview
This skill acts as a deterministic workflow router, managing asynchronous handoffs between Scott (Dev), Sona (QA), and Sophie (Merge/Ops) using live system state (running jobs and PR comments).

## Boundaries & Guardrails
- **Token Efficiency**: This skill does NOT poll. It assumes it has been awakened by an external zero-token script because there is actionable work. 
- **Asynchronous Execution**: ALL agent calls MUST be executed asynchronously.
- **Stateless Operation**: Do NOT create or maintain external state tracking files. Rely entirely on `job_list` and PR comment history as the absolute source of truth.

## Workflow / SOP

### Step 1: Watchdog Protocol (Live System Check)
1. Execute the `job_list` tool to view currently active jobs.
2. Check if any jobs assigned to **Scott** or **Sona** have been running for more than **30 minutes**.
3. If a job is stuck (> 30 mins):
    - Extract the `agent` name and the `initial_prompt` from the `job_list` output.
    - Execute the `job_kill` tool on the stuck job ID.
    - Immediately trigger a new asynchronous `agent_call` using the extracted `agent` and `initial_prompt` to restart the task.

### Step 2: Environment Assessment
1. Check the provided context for open Pull Requests (PRs) and open Issues.
2. For any open PR, read the comment history to determine the current state. Specifically, count how many times **Sona** has left a rejection/failure comment.

### Step 3: Strict Routing
Execute the following conditions strictly based on the environment assessment:
- **Condition A (New/Updated PR)**: IF a PR is open AND Sona hasn't reviewed the latest commit:
    - Dispatch an asynchronous `agent_call` to **Sona** to verify the PR.
- **Condition B (QA Pass)**: IF Sona's latest comment on the PR is a `PASS`:
    - Dispatch an asynchronous `agent_call` to **Sophie** to merge/close the PR.
    - THEN, Dispatch an asynchronous `agent_call` to **Scott** with the next open issue from the backlog.
- **Condition C (QA Fail)**: IF Sona's latest comment on the PR is a `FAIL`:
    - Count the total number of rejection comments Sona has made on this PR.
    - *If total rejections < 3*: Dispatch an asynchronous `agent_call` to **Scott** to address the PR using Sona's feedback.
    - *If total rejections >= 3*: Dispatch an asynchronous `agent_call` to **Scott** to completely drop the current PR and start from scratch.
- **Condition D (Cycle Complete)**: 
    - Once all actionable items are routed, terminate execution. Await the next system wake-up.

## Required Tools
- `job_list`: Required to check the live status, durations, and original prompts of running agent jobs.
- `job_kill`: Required to terminate any jobs that have exceeded the 30-minute watchdog limit.
- `agent_call`: Required to dispatch Scott, Sona, and Sophie asynchronously.
- `gh`: Required to fetch open issues, PRs, and read the comment history on PRs to count QA rejections.
