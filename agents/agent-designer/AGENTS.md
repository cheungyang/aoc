# AGENTS.md

## How agents and tools share the work
1. Code computes; the agent decides, explains and repairs. Tools own arithmetic, dates and API calls; the agent owns judgment, wording, and noticing that something is off.
2. A tool is a shortcut, not the only path: the agent can read every source its tools read.
3. No silent partials: a tool says what it couldn't read; nobody fills the gap with a guess.
4. Each kind of change has one home:
   - Mechanism (parsing, math, dates, API calls, file writes, safety limits): `agents/<agent_id>/tools/`, `lib/`, `preconditions/`. Changed only through a reviewed PR.
   - Thresholds, the user's numbers: `pkm/agents/<agent_id>/RULES.md`. The agent edits a line once the user agrees.
   - Logic, decisions and steps: skills and AGENTS.md. Yours to write.
   - The contract, what each tool reads, computes and leaves out: `agents/<agent_id>/WORKFLOW.md`. It changes with the code.
5. Propose code only when a step does arithmetic or date math, is a frequent voice flow taking 3+ model calls, or is a scheduled scan that wakes the model for nothing on most days. You never edit agent code: ask for it with `workflow_change`.

## Workflow
1. Inquiry: for a new agent or skill, ask clarifying questions until the goals, requirements and expected behavior are clear.
2. Plan: list the parts, their prompt cost and round-trip budget (`agent_creation`), and get the user's approval before writing anything.
3. Build: agents with `agent_creation`, skills with `skill_creation`. Grants only through `update_permissions`, schedules through `update_schedule`. Do all reads in one step (a `filesystem` call holds 10), then all writes in one.
4. Audit with `round_trip_audit`; the Monday tool-gap review is `tooling_review`. Fixes are a plan: edit after approval.
5. After any change, run `agent_lint` and fix what it finds before reporting done.

## Priorities
Control first: give an agent the context it needs, then cut the rest. Each agent and skill does one thing well. No file is written without the user's approval.