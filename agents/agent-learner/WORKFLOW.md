# agent-learner: how its tools work

Kaizen's two tools read and write memory.db only. `friction_query` reads; `submit_proposal` saves a proposal with its checks and edits no file. Both are built on the learning loop's code (core/learning/README.md): the same patterns `!learn` shows, and the checks the evaluator runs, which Kaizen can't read.

## Sources

- **Friction cards**: what the nightly detectors found, with your labels (`!learn label`).
- **Episodes**: the session rows or the turn ledger around a card, cut by code (core/learning/digests.py).
- **The prompt ledger**: each agent's prompt size, snapshotted every night.
- **Proposals**: what Kaizen handed in, and what each check found.

## friction_query

- **patterns**: one agent's cards from one detector, recurring ones first (at least 3 cards in 2 sessions, or a correction saying you'd told the agent before), then by severity. Each carries its card and session counts, severity, extra turns, its newest examples, and the checked proposals already open for it. A pattern is named `<agent>:<detector>`, `?` when no agent was found.
- **precision**: comes with patterns: per detector, cards labeled real out of labeled.
- **cards**: newest first, by agent and detector; `agent="?"` for cards with no agent.
- **episode**: the card's turn and up to 2 before it. From a session table: your words, each tool call with its status and first error line, and the agent's last reply. From the turn ledger: that turn and the ones before it in its session. Every text is clipped and has secrets scrubbed.
- **ledger**: the latest snapshot per agent, its change over a month, and the alerts.
- **proposals**: newest first, with the checks that failed.
- `days` is 1–30; `limit` at most 30.

A turn as the episode view cuts it: the caller tag and time stamp go, the call is paired with its output, and the failure's first error line is kept:

```python
>>> from core.learning.digests import session_turn
>>> session_turn([
...     {"entry_type": "message", "from_role": "user", "message": "<caller>main</caller> [Tue 09:14] Read the topic notes"},
...     {"entry_type": "message", "from_role": "system", "message": 'Tool filesystem: {"instructions": [{"read": "pkm/wiki/topics"}]}'},
...     {"entry_type": "message", "from_role": "system", "message": "Tool Output [0.01s]: <errors>Error: Both 'action' and 'path' are required in each instruction.</errors>"},
...     {"entry_type": "message", "from_role": "ai", "message": "I couldn't read them."},
... ])
{'you': 'Read the topic notes', 'tools': [{'tool': 'filesystem', 'status': 'failed', 'error': "Error: Both 'action' and 'path' are required in each instruction."}], 'agent': "I couldn't read them."}
```

How a pattern is named, with and without an agent:

```python
>>> from agents.agent_learner.lib.views import pattern_key
>>> pattern_key("topic-researcher", "tool_failure"), pattern_key("", "voice_cutoff")
('topic-researcher:tool_failure', '?:voice_cutoff')
```

## submit_proposal

- **What it may change**: an agent's AGENTS.md, SOUL.md or INSTRUCTIONS.md; a skill's SKILL.md; a template under core/prompts/templates/; an agent's RULES.md or FEEDBACK.md in pkm. Never code, agent.json, WORKFLOW.md, IDENTITY.md, the browser hand-off rule, Kaizen's own files, or the learning loop's.
- **Edits** are exact: each `find` must occur once in the file as it is now, and the edits apply in order.
- **Refused outright** (nothing saved): no edits or more than 6, no rationale, no cards or unknown ones, a pattern not named `<agent>:<detector>`, the same change twice, a fourth pattern with a checked proposal in 7 days, or a fourth candidate for one pattern.
- **Checked** (saved either way): where it may land, that each edit applies, size (30 changed lines, a quarter of the file at most), budget, protected behaviours, new wiring, personal information outside pkm, that the file still loads, and agent_lint. All pass: `checked`, waiting for your review; any fails: `failed`, with each reason.
- A checked proposal changes nothing until you approve it; Aki applies it then.

Where a proposal may land:

```python
>>> from core.learning.boundaries import proposable, why_not_proposable
>>> proposable("skills/research/SKILL.md"), proposable("pkm/agents/day-planner/RULES.md")
(True, True)
>>> why_not_proposable("agents/agent-learner/SOUL.md")
'agents/agent-learner/SOUL.md is frozen: a safety rule, or the analyst itself'
>>> why_not_proposable("core/learning/gates.py")
"core/learning/gates.py is the evaluator's: never read, never proposed"
```

An edit applies only when its find occurs exactly once:

```python
>>> from core.learning.gates import apply
>>> apply("List every item due today.", [{"find": "every item due today", "replace": "the 3 highest-impact items"}])
('List the 3 highest-impact items.', [])
>>> apply("a b a", [{"find": "a", "replace": "c"}])[1]
["edit 1's find occurs 2 times, not once: add text around it so it's unique"]
```
