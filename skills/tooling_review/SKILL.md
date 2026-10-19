---
name: tooling_review
description: The Monday tool-gap review. Groups the week's tooling memory entries and proposes, for each group, a RULES.md change, a skill change, or a workflow_change spec.
---
## When to Use
The Monday 09:00 schedule in #agent-management, or when the user asks where agents' tools fell short. Agents log a gap as `Tool gap: <tool>, <file>: <what was missing or asked for>`, and the dream files those entries in `pkm/wiki/memory/topics/tooling.md`.

## Workflow
1. Read `pkm/wiki/memory/topics/tooling.md`, and call `set_thinking("high", ...)` in the same step: the grouping and the choice of path are the heavy step. This week's entries are those seen in the last 7 days (`seen YYYY-MM-DD`); older ones came up in earlier reviews. With none, or no file yet, say so in one line and stop.
2. Group the entries by agent (`src:`) and tool, and merge those that describe the same gap. A count (`x3`) says how often it came up.
3. For each group, read in one step what the choice needs: the tool's section of `agents/<agent_id>/WORKFLOW.md`, the agent's `pkm/agents/<agent_id>/RULES.md`, and the skill that calls the tool. Then pick one path (the table in AGENTS.md):
   - **RULES.md**: a threshold that's wrong for the user. Propose the key, old → new; the agent edits the line once the user agrees.
   - **Skill**: a step or decision the agent got wrong. Propose the change to the skill.
   - **Code**: something the tool doesn't read or compute. Draft a `workflow_change` spec, only when the code rule in AGENTS.md holds; otherwise propose a skill step that works from the raw lines.
   - Nothing: a one-off, or the user's file was broken and is fixed. Say so.
4. Reply in the channel, short enough to be read aloud: one line per group (agent, tool, what fell short, how often), then your proposal. Offer to investigate or draft each change. Change nothing until the user picks.
5. For each change the user picks, follow its skill (`skill_creation`, `workflow_change`), or tell the user what to ask the agent for a RULES.md change. Run `agent_lint` on each agent you changed and fix what it finds before reporting done.

## Example
Three entries from meal-planner: "Tool gap: kitchen_snapshot, INVENTORY.md: rows under 冷凍 have no shelf life" (x3). WORKFLOW.md shows expiry counts from `shelf_life_days`, and frozen items aren't in it. The path: RULES.md, if the user can name the days; a skill step that asks them meanwhile.

## Required Tools
- `filesystem`: to read the tooling topic, each agent's WORKFLOW.md and RULES.md, and the skills involved.
- `set_thinking`: high for the review.
- `agent_lint`: to check each agent you changed.
