---
name: workflow_change
description: Asks the coding graph for a change to an agent's code. Writes a spec with made-up test cases in the shape of the user's real files, queues it, follows it with graph_status to the PR link, and grants a new tool once the PR merges.
---
## When to Use
A change to what an agent's code reads or computes: a new field, a file format a tool must learn, a new tool or scan, a new RULES.md key. Only when the code rule in AGENTS.md holds. Not for a threshold that already exists (the agent edits its RULES.md) or for steps and decisions (edit the skill). You never write agent code, its tests or WORKFLOW.md yourself: the coding graph builds them and the user reviews the PR.

## Workflow
1. In one step, read what the change touches:
   - the tool's module and its helpers: `agents/<agent_id>/tools/`, `agents/<agent_id>/lib/`;
   - its section of `agents/<agent_id>/WORKFLOW.md`, and `lib/rules.py` if any;
   - its tests, in `tests/agents/`;
   - the user's real files it reads (the docstring's `Reads:` line), and the memory entry or message that asked for the change.
2. Write the spec to `pkm/wiki/software/aoc/specs/<agent_id>-<tool>_v1.md` (the next version if one exists), with these sections:
   - **Context**: the agent, the tool, and the gap in the user's words.
   - **Reads**: every source by path; which are required (missing means `failed`) and which optional (`partial`).
   - **Returns**: the facts, field by field, in plain words. Facts only: choices stay in the skill.
   - **Issues**: what makes the result `partial` (unrecognized lines come back raw) or `failed`. Never a guess.
   - **RULES.md**: each new threshold's key, default and plain-words description, for `lib/rules.py`.
   - **WORKFLOW.md**: each rule in plain words, with a worked example: made-up literal inputs to a pure `lib/` function, then the exact result.
   - **Tests**: made-up cases in the shape of the user's real files: the same format, invented names, places, amounts and dates. Never copy a line from the user's files: the repo is public, and `tests/test_repo_privacy.py` fails on 8 words in a row taken from a note. Each case has its expected output; add the broken shapes the tool must report.
   - **Files**: what may change: `agents/<agent_id>/tools/`, `lib/`, `preconditions/`, WORKFLOW.md and `tests/agents/<package>/`.
   - **Done when**: `.venv/bin/python -m pytest -q tests/agents/<package> tests/agents/test_workflow_examples.py tests/agents/test_workflow_contract.py` passes.
3. Show the user a short summary of the spec and wait for approval.
4. Queue it: read `pkm/wiki/software/aoc/build_request.json`, add one task to its `queue` (`task_id`, `spec_path`, `status: "queued"`, `dependencies`, `allowed_files`, `verification_command`: the Done-when command), then `overwrite` the file. The graph owns every other field. If the file doesn't exist, stop: ask the user to set up the aoc project's manifest.
5. Call `graph_call(graph_name="coding", query="project_name: aoc")` to start a tick, then `graph_status(graph_name="coding")` to see the task. The graph moves on a 5-minute tick: check again when the user asks, never in a loop.
6. When the task shows a PR, give the user the link and a one-line summary. They approve it on GitHub. A halted task: tell the user its last error, and fix the spec or the manifest entry.
7. After the PR merges, grant a new tool with `update_permissions`. Run `agent_lint` on the agent and fix what it finds before reporting done.

## Example
meal-planner's memory says "Tool gap: kitchen_snapshot, INVENTORY.md: rows under 冷凍 have no shelf life". The spec: Reads INVENTORY.md (required) and RULES.md; Returns each row's days left, marked "est"; Issues: a row with no rule comes back raw, `partial`; RULES.md: frozen items in `shelf_life_days`; a WORKFLOW.md example for one invented frozen row; three tests with made-up rows in the 冷凍 section's format.

## Required Tools
- `filesystem`: to read the agent's code, tests and WORKFLOW.md and the user's files, and to write the spec and the manifest entry in `pkm/wiki/software/`.
- `graph_call`: to start the coding graph's tick.
- `graph_status`: to follow the task to its PR.
- `agent_lint`: to check the agent.
