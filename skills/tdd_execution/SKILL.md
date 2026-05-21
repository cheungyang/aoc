---
name: tdd_execution
description: Orchestrates Test-Driven Development (TDD) workflow, branching logic, and a strict 3-attempt failure guardrail within an agent-scoped workspace.
---

## Overview
This skill acts as an operational harness for coding agents. It enforces the Red/Green Test-Driven Development (TDD) loop, manages Git branching, and imposes a hard stop after 3 failed test attempts to prevent infinite LLM hallucination loops.

## Workspace Constraint
ALL `git`, `bash_terminal`, and `filesystem` operations MUST take place strictly inside your scoped workspace directory: `pkm/agents/<agent_id>/workspace/`. You do not have permission to modify files outside of this boundary.

## When to Use
Use this skill for every new software issue pulled from GitHub. This is the mandatory execution cycle for implementing code.

## Workflow

### 1. Context & Branch Setup
- Navigate to `pkm/agents/<agent_id>/workspace/` via `bash_terminal`.
- Run `git checkout main` followed by `git pull` to get latest code.
- Run `git checkout -b feature/<issue_id>` to create a working branch.
- Use the `gh` tool to read the assigned GitHub issue.
- Extract the exact spec path from the issue description and use the `obsidian` tool to read the Markdown spec.
- **Strict Rule:** DO NOT browse the broader codebase. Rely ONLY on the Spec and the Issue.

### 2. The Red Phase (Write Failing Test)
- Read the Acceptance Criteria from the Spec.
- Use `filesystem` to write the test file within your scoped workspace.
- Execute the test via `bash_terminal` (e.g., `npm test`, `pytest`, `cargo test`).
- **Assertion:** The test MUST fail. If it passes without implementation code, the test is invalid. Rewrite the test.

### 3. The Green Phase (Implementation)
- Write the implementation code using `filesystem` to satisfy the failing test.
- Re-run the tests via `bash_terminal`.
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
- `gh`: Needed to read the assigned issue and create PRs or leave "Blocked" comments.
- `bash_terminal`: Needed to run Git commands and execute testing frameworks within your workspace.
- `obsidian`: Needed to read the specific Markdown spec file created by the Planner agent.
- `filesystem`: Needed to write and modify the test and implementation files in your `pkm/agents/<agent_id>/workspace/`.