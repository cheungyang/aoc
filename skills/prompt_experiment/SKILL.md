---
name: prompt_experiment
description: Drafts one small, testable change to an agent's prompt text for a friction pattern, and hands it in with submit_proposal.
---
## Core

1. Read the whole target file first, and find the exact text behind the friction.
2. Draft one to three candidates, each the smallest change that would fix the cards: reword or remove a rule that isn't working before you add one. When the file is over its budget, an addition must remove at least as much.
3. Write each change as exact edits: `find` copied from the file, with enough words around it to occur once; `replace` the new text, or "" to remove it. At most 6 edits and 30 changed lines.
4. Hand each candidate in with `submit_proposal`: the path, the edits, the pattern ("<agent>:<detector>"), a two-sentence rationale tied to the cards, the card ids in `fixes`, and in `expected` what the agent should do differently.
5. A failed check comes back with its reason: fix the draft and hand it in again, or drop it and say why. Never word around a protected, capability or personal check: those changes are the user's.
6. A personal detail never goes into a repo file. If the lesson needs one, its home is the agent's FEEDBACK.md or RULES.md in pkm.

## Reference

### What a proposal may change
An agent's AGENTS.md or SOUL.md, a skill's SKILL.md, a template under `core/prompts/templates/`, and an agent's RULES.md or FEEDBACK.md in pkm. Never code, agent.json, WORKFLOW.md, IDENTITY.md, the browser hand-off rule, your own files, or the learning loop's.

### What the checks look for
- the edits apply to the file as it is now;
- the change is small, and what it removes survives elsewhere or is listed in `dropped`;
- the file stays within its budget;
- no rule about approvals, honesty, refusals, health and finance, or safety is removed or weakened;
- no new tool, path, channel, link, address or grant: wiring is the user's to change;
- nothing personal outside pkm;
- the file still loads (a template keeps its tags, a skill its sections);
- agent_lint finds nothing new.

### A good candidate
- One idea per proposal: two fixes are two proposals.
- It names the behaviour, not the incident: "Lead with the 3 highest-impact items" beats a rule about one Tuesday.
- `retires` quotes any FEEDBACK entry the change makes redundant, so it can go when the change lands.

### After it's checked
A checked proposal waits for the user (`!learn proposals`). Once approved, Aki applies it; you never edit a file.
