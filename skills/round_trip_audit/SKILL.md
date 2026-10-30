---
name: round_trip_audit
description: Audits an agent for model round trips, phantom references, prompt budget and how its work is split (tools on @agent_tool, decisions in skills) with agent_lint, and proposes the fixes as a plan.
---
## When to Use
The user asks to audit an agent, or why one is slow, loops, or fails a read or a skill load. Not for creating agents (`agent_creation` already runs `agent_lint`).

## Workflow
1. In one step, run `agent_lint` on the agent and read its `AGENTS.md`, `INSTRUCTIONS.md`, its `WORKFLOW.md`, and the SKILL.md of the skills it runs most (one `filesystem` call holds up to 10 reads).
2. Check each finding against the cited line. Drop false leads, such as a path the agent only passes to another agent or graph, or an example path; keep the rest.
3. Read anything still needed in one step, asking for `high` thinking: the analysis below is the heavy step. List the agent's workflows, user-facing first; for a voice-room agent, the hot flow is the one a spoken request starts. For each, write out its sequential model calls: every step that waits on tool results is one call however many tools it runs, a `load_skill` is a step, and the reply is one more.
4. Walk the Round-trip Budget checklist (`agent_creation`) over each workflow, noting which item removes which call. Voice-hot agents target ≤ 2–3 calls per user turn.
5. Check how the work is split (the table in AGENTS.md):
   - Every tool in `agents/<agent_id>/tools/` is an `@agent_tool` with `Reads:`, `Computes:` and `Leaves out:` lines, and WORKFLOW.md covers it. `agent_lint` reports gaps as `workflow_missing` and `contract_gap`.
   - Decisions sit in skills: a tool that picks, ranks for judgment, or words the reply is doing the agent's job. Move the choice to the skill and keep the facts in the tool.
   - Numbers the user owns sit in RULES.md, not in code or skill text. A literal threshold in a skill becomes its RULES.md key.
   - The agent can read every source its tools read (`read_not_granted`).
6. For each schedule in the report: an `always` precondition where a scan type fits → that scan; a first step that re-reads what the scan read → work from its `<findings>`.
7. Report in Markdown:
   - a table: workflow | calls today → target | fixes;
   - the confirmed findings, by kind, each with its fix;
   - budget: persona and preload against their caps, and what to cut or preload;
   - how the work is split: decisions to move into skills, thresholds to move to RULES.md;
   - what needs code (a tool, a tool action or a scan): a spec for `workflow_change`, only where the code rule in AGENTS.md holds;
   - the edits you propose, as a plan.
8. Edit nothing until the user approves; then edit under the `update_permissions` and `update_schedule` rules. Run `agent_lint` and fix what it finds before reporting done.

## Examples of Fixes
- fitness-coach setup, 6 → 2 calls: `workout_setup` returns recovery, eligible exercises with next loads, sleep and the time in one call; the skill relays it.
- day-planner morning plan, 12–20 → 1–4: the `morning_briefing` scan computes dates, chores and fixed reads before the run and hands them over as findings.
- A tool that chose which project to coach → it returns the ranking and its reasons; the skill decides.
- A skill that said "alert above 4%" → the skill names `move_pct`; the number lives in RULES.md.
- A log read, edited and overwritten → one `append`.
- A skill loaded on most turns → preloaded (`full` if small, `core` if large) within the cap.

## Required Tools
- `agent_lint`: the agent's findings, budgets, tools, skills and schedules.
- `filesystem`: to read the agent's files, its code and WORKFLOW.md, its RULES.md and its skills.
