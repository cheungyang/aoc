---
name: tdd_execution
description: Orchestrates Test-Driven Development (TDD) workflow, branching logic, and a strict 3-attempt failure guardrail within an agent-scoped workspace.
---

## Overview
This skill acts as an operational harness for coding agents. It enforces the Red/Green Test-Driven Development (TDD) loop, manages Git branching, and imposes a hard stop after 3 failed test attempts to prevent infinite LLM hallucination loops.

## Execution Guardrails
- **Workspace Constraint**: ALL `git`, `bash`, and `filesystem` operations MUST take place strictly inside your scoped workspace directory: `pkm/agents/<agent_id>/workspace/`. You do not have permission to modify files outside of this boundary.
- **Anti-Stall (No Interactive Prompts)**: You are an autonomous background agent and cannot interact with terminal prompts (e.g., password prompts, Y/N confirmations). If you anticipate a command might prompt for input, always use non-interactive flags. If any command hangs or blocks requiring interactive input, you MUST abort the operation immediately, stop execution, and surface the stall as a clear error to the Orchestrator/User.

## When to Use
Use this skill for every new software issue pulled from GitHub. This is the mandatory execution cycle for implementing code.

## Workflow

### 1. Context & Branch Setup
- Navigate to `pkm/agents/<agent_id>/workspace/` via the `bash` tool.
- **CRITICAL AUTHENTICATION STEP**: Before interacting with git, you MUST run `gh auth setup-git` using the `gh` tool. This configures git to use the GitHub CLI for authentication and prevents terminal stalls when pushing or pulling.
- Run `git checkout main` followed by `git pull` to get latest code.
- Run `git checkout -b feature/<issue_id>` to create a working branch.
- Use the `gh` tool to read the assigned GitHub issue.
- Extract the exact spec path from the issue description and use the `obsidian` tool to read the Markdown spec.
- **Strict Rule:** DO NOT browse the broader codebase. Rely ONLY on the Spec and the Issue.

### 2. The Red Phase (Write Failing Test)
- Read the Acceptance Criteria from the Spec.
- Use `filesystem` to write the test file within your scoped workspace.
- Execute the test via the `bash` tool (e.g., `npm test`, `pytest`, `cargo test`).
- **Assertion:** The test MUST fail. If it passes without implementation code, the test is invalid. Rewrite the test.

### 3. The Green Phase (Implementation)
- Write the implementation code using `filesystem` to satisfy the failing test.
- Re-run the tests via the `bash` tool.
- Repeat until the test passes, **up to a maximum of 3 attempts**.

### 4. The Infinite Loop Guardrail (3-Attempt Limit)
If the test fails on the 3rd attempt, you MUST execute the **Abort Sequence**:
1. Run `git checkout main`.
2. Run `git branch -D feature/<issue_id>` to delete the failing branch.
3. Use the `gh` tool to leave a comment on the GitHub issue: *"Status: Blocked. Reached 3-attempt limit. Returning to Orchestrator."*
4. Halt execution for this issue and await reassignment.

### 5. The Success Sequence
If the test passes within the 3 attempts:
1. Run `git add .`, `git commit -m "feat: implement <issue_id>"`, and `git push -u origin feature/<issue_id>`.
2. Use the `gh` tool to create a Pull Request against the `main` branch.
3. Include the Spec path and Issue ID in the PR description so the QA agent can review it.

## Required Tools
- `gh`: Needed to configure git authentication, read the assigned issue, create PRs, or leave "Blocked" comments.
- `bash`: Needed to run testing frameworks within your workspace.
- `git`: Needed to run Git commands.
- `obsidian`: Needed to read the specific Markdown spec file created by the Planner agent.
- `filesystem`: Needed to write and modify the test and implementation files in your `pkm/agents/<agent_id>/workspace/`.