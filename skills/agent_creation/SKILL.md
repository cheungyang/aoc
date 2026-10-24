---
name: agent_creation
description: Designs and creates a new agent from a worksheet that classifies each step of its workflow (config, skill, RULES.md, tool or scan, memory, schedule), ending with the classification table for the user to confirm.
---
## Core

Use this skill when the user asks for a new agent. Plan first; write nothing until the user approves the plan.

### Guardrails
- Change no other agent or system file, except the routing entries a new channel needs (`update_permissions`).
- `SOUL.md` must match the description and tools in `agent.json`.
- Budgets: persona under 3,500 chars, preloaded skills under 6,000, a voice flow 2–3 model calls a turn (Reference).

### Design worksheet
1. List the workflow's steps: what starts each one (a message, voice, a schedule), what it reads, what it decides, what it writes.
2. Classify each step by the homes in your AGENTS.md; the table is for the plan and report, never a file. Call `set_thinking("high", ...)` in the same step as the reads it needs; this is the heavy step.
   - agent config: `agent.json` and the persona;
   - skill: steps and decision logic;
   - RULES.md: a number that expresses the user's preference;
   - tool or scan: only when the code rule in AGENTS.md holds;
   - memory topic: facts several agents share (`pkm/wiki/memory/TAGS.md`);
   - schedule: recurring work, gated by a precondition or scan.
3. Present the plan: the classification table, the prompt cost, and the model calls per workflow. Wait for approval.
4. Draft the files in the layout (Reference): `agents/<agent_id>/` gets `agent.json`, `AGENTS.md` (operating instructions: which skill each workflow follows), `SOUL.md` and `IDENTITY.md`; `pkm/agents/<agent_id>/` gets `USER.md`, `memory_logs/`, `MEMORY.md` and `FEEDBACK.md`. Build `agent.json` from the template under `update_permissions`; schedules go through `update_schedule`.
5. RULES.md defaults, only for numbers its code reads (a fixed convention is skill text): give each threshold a key, a default and a plain-words description. Write `pkm/agents/<agent_id>/RULES.md` yourself, one `- key: value  # description` line each. The same keys go in the code spec, as `lib/rules.py`.
6. Code (tools, scans, `lib/`, WORKFLOW.md): ask for it with `workflow_change`. Never write it yourself. A new tool is granted only after its PR merges, in `workflow_change`'s last step.
7. Run `agent_lint` and fix what it finds before reporting done. Findings that wait on the PR (RULES.md keys no code reads yet, text naming a tool not built yet) stay; name them in the report.
8. Report as the Final Report says, ending with the classification table for the user to confirm.

Call `load_skill("agent_creation")` before writing any file or the report, for the templates, the budgets and the report format.

## Reference

### Classifying a step
The table in AGENTS.md decides. Examples:

| Step | Kind | Why |
|---|---|---|
| Snap a load to a buildable weight | tool | arithmetic the model gets wrong at low thinking |
| "Alert me on a 3% move" | RULES.md (`move_pct: 3`) | a number the user owns |
| Pick which project to coach, and how | skill | judgment |
| What a tool reads, computes and leaves out | WORKFLOW.md | the contract; it changes with the code |
| Skip the 21:00 check-in when dinner is logged | scan | a schedule that would wake the model for nothing |
| The user's cards, shared by two agents | memory topic | facts more than one agent needs |

Safety limits stay in code, not RULES.md: they aren't preferences (an API budget, a duplicate check).

### Prompt Budget
Paid on every turn, forever:

| File | Target | Hard ceiling |
|---|---:|---:|
| `AGENTS.md` | 1500 chars | 2500 |
| `SOUL.md` | 1000 chars | 1500 |
| `USER.md` | 400 chars | 800 |
| `IDENTITY.md` | 250 chars | 400 |
| `INSTRUCTIONS.md` (if used) | 0 | 2500 |

Keep authored persona under 3500 characters. Preloaded skill text is capped at 6,000 characters per agent (a test enforces it).

A paragraph written once is paid for thousands of times. Memory rides on the same prompt (the shared Profile, the agent's `memory_topics`, its `MEMORY.md` and `FEEDBACK.md`), about 2,000 tokens for an active agent. Each bound tool schema costs 130–550 tokens a turn. An on-device agent (`"provider": "local"`) must fit its prompt, schemas and conversation in about 4,096 tokens; above ~2,000 tokens of static prompt it can't run locally.

- One precise sentence beats three hedging ones. Never state a rule in both `SOUL.md` and `AGENTS.md`.
- Examples go in skills, which cost nothing until loaded; the persona never unloads.
- Facts about the user never go in the repo (it's public). An agent's one `USER.md` lives in `pkm/agents/<agent_id>/`; facts several agents share go in the Profile and topics. Never pre-seed memory files.
- Subscribe to `memory_topics` sparingly: each costs prompt on every turn.

### Round-trip Budget
Voice-hot agents target 2–3 model calls per user turn; the plan states each workflow's count. Checklist:
1. List each workflow's sequential calls (tools called in one step count once).
2. Fixed reads → a context tool, or a scan for a schedule.
3. Math (dates, totals, thresholds) → code: a tool returns the numbers (the code rule in AGENTS.md).
4. Independent calls → one step.
5. `append` or `replace_block` over a read and an overwrite.
6. Preload always-used skills.
7. Scan-gated schedules, not `always`.
8. Voice-hot agents speak one short sentence before tools.

### Voice Agents
A voice agent gets each turn as text: the transcript of what you said, once a pause ends your turn, and it answers every turn. It can't interrupt you or hear the audio. Words the transcriber missed arrive as a sentence that doesn't make sense: the agent asks you to repeat that part, and never guesses.

### Config Decisions
- `model`: a tier, never a version string: `FLASH_LITE` for narrow, high-volume work, `FLASH` for everyday reasoning, `PRO` only when the agent needs the strongest model.
- `skills`: an object; the list form is rejected. `{"preload": "full"}` for a small skill used on nearly every run, `{"preload": "core"}` for a larger one used often, `{}` for rare ones.
- `thinking`: usually omitted (defaults: voice `low`, text `medium`, scheduled `high`). Override a surface whose work needs another depth, e.g. `{"text": "high"}` for a PRO agent doing deep synthesis in chat, or `{"text": "low"}` when most turns are quick and one skill step raises it.
- `set_thinking` (tool): only for a mixed workload, quick turns plus one heavy step (a plan, a many-constraint choice); that skill names the step and level.
- `voice`: every user-facing agent gets a Chirp 3 HD voice matching its personality (and gender, where the name suggests one), different from every other agent's: there is one voice room, and the voice is how the user hears who is answering. Only internal workers omit it.

### agent.json Template
You MUST use this exact JSON structure:
```json
{
  "agent_id": "<agent_id>",
  "channels": ["<Channels where this agent could be active>"],
  "name": "<Agent Name>",
  "emoji": "<Emoji>",
  "description": "<Short Description>",
  "model": "<Optional Tier: FLASH_LITE | FLASH | PRO>",
  "provider": "<Optional: google (default) | local>",
  "memory_topics": ["<Optional shared topic>"],
  "tools": {
    "<tool_name_1>": {}
  },
  "skills": {
    "research": { "preload": "core" },
    "weekly_review": {}
  },
  "thinking": { "text": "high" },
  "voice": { "name": "en-US-Chirp3-HD-Zephyr", "rate": 1.1 }
}
```
*Critical Constraints for agent.json:*
- Remove every optional key the agent doesn't use rather than leaving a placeholder. `skills`, `thinking` and `voice` show example values: replace them with the agent's own, and drop `thinking` unless the plan calls for it, and `voice` only for an internal worker.
- Use only the keys above, plus `schedules`. Validation rejects unknown top-level keys, and an `agent.json` that fails validation is not loaded at all. `stateless`, `graph`, `concierge_prefixes`, `voice_config`, `discord_token_key` and `channel_hosts` are system keys (internal workers, and the concierge, which runs the only bot and hosts every channel); new agents don't use them. A new agent's channel goes in `channels`, and is registered with the concierge as `update_permissions` describes (rule 10).
- The `tools` field MUST be a dictionary object (e.g., `"tools": { "tool_name": {} }`), NOT an array.
- Do not explicitly add tools to the `tools` object if they are already inherited via the skills in `skills` (e.g., `web_search` is inherited via the `research` skill).
- `skills` MUST be an object keyed by skill id. Each value is `{}` (on demand: the agent sees a one-line entry and calls `load_skill`), `{"preload": "full"}` (the whole SKILL.md body is in every prompt) or `{"preload": "core"}` (only its `## Core` section is; `load_skill` returns the rest). Preload only skills the agent uses on most runs, keep the agent's preloaded text within 6,000 characters, and preload as `core` only skills split into `## Core` / `## Reference` (see `skill_creation`).
- Omit `schedules` unless the agent needs recurring work. To add schedules, load the `update_schedule` skill: every entry needs a `kind` and, for prompt schedules, a `precondition`, and it must pass `schedule_validate` before it is saved. Invalid entries are rejected at load and never run.
- The `model` field names a **tier**, never a version string like `gemini-3.5-flash-lite` or an on-device build id. This holds for every provider. Valid tiers are defined per provider in `core/util/models.py`; omit the field to accept the default.
- The `provider` field selects where the model runs. Omit it for Gemini. `"local"` routes the agent to the on-device server configured by `LOCAL_LLM_BASE_URL`. Both providers answer the same tier names, so moving an agent on-device is adding this one key and nothing else.
- `thinking` (optional, rare) overrides the code defaults for the surfaces it names. Keys: `voice` | `text` | `scheduled`; values: `minimal` | `low` | `medium` | `high`. PRO has no `minimal` (it's raised to `low`). A schedule entry's own `thinking_level` wins on that run (see `update_schedule`).
- `voice` (optional): `name` must be a Chirp 3 HD voice from `CHIRP3_HD_VOICES` in `core/voice/foundation/settings.py` (the catalog lists each voice's gender and style), written `en-US-Chirp3-HD-<Voice>`; `rate` is optional, 0.25–2.0. Every agent the user talks to has one, and no two agents share a voice: one `filesystem` `grep` for `Chirp3-HD-` in `agents/` lists the voices taken; pick one nobody has whose gender and style fit the persona, and name it in the plan. `agent_lint` reports a voice taken, unknown or missing, with the free ones (`voice_taken`, `unknown_voice`, `voice_missing`). Only internal workers omit it and speak in `DEFAULT_VOICE`. Settings shared by all agents live in `core/voice/foundation/settings.py`, never in agent.json.

*Before setting `"provider": "local"`, know two things:*
- **The server handles one request at a time.** Local agents queue behind each other, so keep the local roster small and prefer low-volume, single-purpose agents.
- **Never put a mid-turn caller on it.** A component that invokes a model *inside* another turn's flow — `graph-worker-low` (used by `ContextPruner`) — would queue behind the very turn it is serving, stalling it. It stays on Gemini.

### Persona Files
The prompt loads these on every turn, so each holds one thing, in the shape every other agent uses (e.g. `agents/fitness-coach/`, `agents/goal-setter/`):

- `AGENTS.md` is the agent's purpose and workflow: its operating instructions, short and numbered, naming the skill each workflow follows. It never describes the agent's config, and never holds the classification table, which is for the plan and the report.
- `SOUL.md` is its persona and tone: who it is ("You are <Name>, ...") and how it sounds.
- `IDENTITY.md` has four lines, with the values in `agent.json`.

```markdown
# AGENTS.md

## Operating Instructions
1. **<When it starts>**: follow the `<skill>` skill. <The one rule it needs that the skill doesn't say.>
2. **<Next workflow>**: ...
```

```markdown
# IDENTITY.md

Agent ID: <agent_id>
Name: <name>
Emoji: <emoji>
Description: <description>
```

`agent_lint` reports an `IDENTITY.md` without those lines, or an `AGENTS.md` holding the classification table (`persona_format`).

### Layout
```
agents/<agent_id>/
  agent.json, AGENTS.md, SOUL.md, IDENTITY.md   the definition: yours to write
  WORKFLOW.md          the contract: what each tool reads, computes and leaves out, with examples the tests run
  tools/<name>.py      its tools, loaded only for this agent, when granted
  preconditions/<type>.py   its scans, only for its own schedules
  lib/                 helpers its tools and scans share
  lib/rules.py         the thresholds its code reads: keys, defaults, descriptions
pkm/agents/<agent_id>/
  USER.md              about its human: never in the repo, which is public
  RULES.md             the user's values for those thresholds; the agent edits a line once the user agrees
  MEMORY.md, FEEDBACK.md, memory_logs/
```
Python imports the code as `agents.<agent_pkg>` (`agent_pkg`: the id with `_` for `-`). `tools/` at the repo root holds only tools general enough to share. You can read agent code but not change it: the code, its tests and WORKFLOW.md change through a reviewed PR (`workflow_change`).

RULES.md is a plain list, one rule per line, `- key: value  # description`. A table rule is `- key:` with indented `  - name: value` lines below it. A missing line means the default applies. Every agent can already write its own `pkm/agents/<agent_id>/` folder, so RULES.md needs no grant.

### Final Report
Once the files are written, reply in plain Markdown, never XML (your reply is shown in Discord):
- The new agent's id and what it specializes in, with its tools and skills.
- The files created: `agents/<agent_id>/agent.json`, `SOUL.md`, `AGENTS.md` and `IDENTITY.md`, plus `pkm/agents/<agent_id>/USER.md`, `MEMORY.md`, `FEEDBACK.md` and `RULES.md`.
- The code asked for with `workflow_change`, and its PR link once there is one.
- Its round-trip budget (model calls per workflow), and any `agent_lint` finding you left in place, with why.
- Any filesystem errors.
- Last, the classification table for the user to confirm: step | kind | where it lives | why.

**Memory Trigger**: End with a `<system_memory_log>` block recording what you learned about agent design, context size or the user's preferences.

### Required Tools
- `filesystem`: to read reference files (the tiers in `core/util/models.py`, the voice catalog in `core/voice/foundation/settings.py`) and write the files in `agents/<agent_id>/` and `pkm/agents/<agent_id>/`.
- `set_thinking`: high for the classification step.
- `agent_lint`: to check the new agent's files, grants and budgets before the final report.