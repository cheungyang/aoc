---
name: qa_evaluation
description: Orchestrates Pull Request evaluation, dynamic testing, and strict logic/anti-pattern checks.
---

## Overview
This skill guides the QA Agent through validating code against strict specifications. It enforces dynamic test execution, cross-references implementation against Acceptance Criteria, and hunts for specific LLM anti-patterns and code smells.

## Workspace Constraint
All `git`, `bash`, and `filesystem` operations must take place inside your scoped workspace: `pkm/agents/<agent_id>/workspace/`. 

## Workflow

### 1. Setup & Context
- Use `git` (clone) to pull the repo into your workspace.
- Use `gh` to read the target Pull Request.
- Use `git` (branch) to checkout the PR's feature branch.
- Extract the linked Spec path from the PR and read it using `filesystem`.

### 2. Phase 1: Dynamic Execution (The Machine Test)
- Execute the test suite via `bash` (e.g., `npm run test`, `pytest`).
- **Condition:** If the tests fail to execute or fail their assertions, the PR is instantly rejected. Proceed to The Verdict.

### 3. Phase 2: Static Evaluation (The Logic Test)
- Use `filesystem` to read the modified files.
- Cross-reference the code against the `Given... When... Then...` Acceptance Criteria in the Spec.
- **Actively hunt for the Anti-Pattern Library:**
  1. **The "Fake It" Trap**: Hardcoded mock data passing tests without real logic.
  2. **The "Happy Path" Bias**: Missing null checks, unhandled undefined variables, or malformed JSON handlers.
  3. **The "Silent Failure"**: Swallowed errors without logging or bubbling up.
  4. **The "Hallucinated Import"**: Using libraries not specified in the Spec.
  5. **The "Infinite Await"**: Unresolved promises or missing network timeouts.
- **Actively hunt for Code Smells (Refactoring Triggers):**
  1. **WET Code**: If similar/identical code blocks repeat multiple times, demand refactoring to generalize the functions.
  2. **Bloated Files**: If a file exceeds 150 lines (excluding tests), demand it be broken down into smaller files.
  3. **Spaghetti Dependencies**: If you see excessive cross-file referencing, especially circular references, demand architectural refactoring.

### 4. The Verdict (Human-in-the-Loop)
- **Approve**: If all tests pass and no anti-patterns/code smells are found, use `gh` to leave the review: "QA Passed - Awaiting Human Merge". Do NOT merge it yourself.
- **Reject**: Use `gh` to "Request Changes". You MUST quote:
  - The exact line of code.
  - The specific Spec rule, Anti-Pattern, or Code Smell violated.
  - The terminal output if a test failed.
  Assign the PR back to the coding agent.

### 5. Loop Guardrail (3-Strike Breaker)
Check the PR history. If you have rejected this specific PR 3 times:
1. Close the PR via `gh`.
2. Delete the branch via `git`.
3. Label the original issue `status: blocked - QA failed`.
4. Halt and await Orchestrator intervention.

## Required Tools
- `gh`: Read PRs, leave reviews, and assign statuses.
- `git`: Clone repos and switch to feature branches.
- `bash`: Run test suites dynamically.
- `filesystem`: Read the Markdown specs and the codebase in `pkm/agents/<agent_id>/workspace/`.