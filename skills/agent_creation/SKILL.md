---
name: agent_creation
description: Skill for creating new agents with required files and vault structures, ending with a Markdown report.
---
## Core

Use this skill only when the user explicitly requests the creation of a new agent persona.

### Guardrails
- **Strict Approval**: NEVER create an agent or write configuration files without first presenting a detailed plan (purpose, tools, skills, SOUL, prompt cost) and receiving explicit user approval.
- **Identity Consistency**: `SOUL.md` must strongly align with the description and tools in `agent.json`.
- **System Isolation**: Do not modify existing agents or system files, except the routing entries a new channel requires (`update_permissions` rule 7).

### Workflow
1. Create `agents/<agent_id>/`.
2. Build `agent.json` from the Reference template and write it by following the `update_permissions` skill.
3. Write `SOUL.md` (persona, tone, boundaries), `AGENTS.md` (operating instructions, rules, priorities), `USER.md` (the human, and how to address them) and `IDENTITY.md` (Agent ID, Name, Emoji, Description).
4. Create `pkm/agents/<agent_id>/memory_logs`, `pkm/agents/<agent_id>/MEMORY.md` and `pkm/agents/<agent_id>/FEEDBACK.md`.
5. Report to the user in Markdown, as the Reference's Final Report describes.

### Prompt Budget
These files are in the system prompt on every turn, forever:

| File | Target | Hard ceiling |
|---|---:|---:|
| `AGENTS.md` | 1500 chars | 2500 |
| `SOUL.md` | 1000 chars | 1500 |
| `USER.md` | 400 chars | 800 |
| `IDENTITY.md` | 250 chars | 400 |
| `INSTRUCTIONS.md` (if used) | 0 | 2500 |

Keep authored persona under 3500 characters. Preloaded skill text (full bodies plus Core sections) is capped at 6,000 characters per agent; a test enforces it.

### Config Decisions
- `model`: a tier, never a version string: `FLASH_LITE` for narrow, high-volume work, `FLASH` for everyday reasoning, `PRO` only when the agent genuinely needs the strongest model.
- `skills`: an object; the list form is rejected. `{"preload": "full"}` for a small skill used on nearly every run, `{"preload": "core"}` for a larger one used often, `{}` for rare ones.
- `thinking`: usually omitted. Defaults are voice `low`, text `medium`, scheduled `high`; override a surface only when its work needs a different depth, e.g. `{"text": "high"}` for a PRO agent doing deep synthesis in chat.
- `voice`: a user-facing agent whose name suggests a voice gets a Chirp 3 HD voice matching its gender and personality; neutral names (Concierge, Rio) omit it.

Call `load_skill("agent_creation")` before writing `agent.json` or the final report, for the template, its constraints and the report format.

## Reference

### Overview
This skill guides agents on how to create a new agent in the system, including workspace files and Obsidian vault folders. It ends with a short Markdown report for the user.

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
  "discord_token_key": "<Optional Discord Token Key>",
  "channel_hosts": ["<Optional Channel>"],
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
- Remove every optional key the agent doesn't use rather than leaving a placeholder. `skills`, `thinking` and `voice` show example values: replace them with the agent's own, and drop `thinking` and `voice` unless the plan calls for them.
- Use only the keys above, plus `schedules`. Validation rejects unknown top-level keys, and an `agent.json` that fails validation is not loaded at all. `stateless`, `graph`, `concierge_prefixes` and `voice_config` are system keys (internal workers, the concierge, the bots that join voice); new agents don't use them.
- The `tools` field MUST be a dictionary object (e.g., `"tools": { "tool_name": {} }`), NOT an array.
- Do not explicitly add tools to the `tools` object if they are already inherited via the skills in `skills` (e.g., `web_search` is inherited via the `research` skill).
- `skills` MUST be an object keyed by skill id. Each value is `{}` (on demand: the agent sees a one-line entry and calls `load_skill`), `{"preload": "full"}` (the whole SKILL.md body is in every prompt) or `{"preload": "core"}` (only its `## Core` section is; `load_skill` returns the rest). Preload only skills the agent uses on most runs, keep the agent's preloaded text within 6,000 characters, and preload as `core` only skills split into `## Core` / `## Reference` (see `skill_creation`).
- Omit `schedules` unless the agent needs recurring work. To add schedules, load the `update_schedule` skill: every entry needs a `kind` and, for prompt schedules, a `precondition`, and it must pass `schedule_validate` before it is saved. Invalid entries are rejected at load and never run.
- The `model` field names a **tier**, never a version string like `gemini-3.5-flash-lite` or an
  on-device build id. This holds for every provider. Valid tiers are defined per provider in
  `core/util/models.py`; omit the field to accept the default. Choose `FLASH_LITE` for narrow,
  high-volume work, `FLASH` for everyday reasoning, and `PRO` only when the agent genuinely
  needs the strongest model.
- The `provider` field selects where the model runs. Omit it for Gemini. `"local"` routes the
  agent to the on-device server configured by `LOCAL_LLM_BASE_URL`. Because both providers
  answer the same tier names, moving an agent on-device is adding this one key and nothing
  else — its `model` line is untouched.
- `thinking` (optional, rare) overrides the code defaults (voice `low`, text `medium`, scheduled `high`) for the surfaces it names. Keys: `voice` | `text` | `scheduled`; values: `minimal` | `low` | `medium` | `high`. Use it only when the agent's typed or scheduled work needs a different depth than the default, e.g. `{"text": "high"}` for a PRO agent doing deep synthesis in chat. PRO has no `minimal` (it's raised to `low`). A schedule entry's own `thinking_level` wins on that run (see `update_schedule`).
- `voice` (optional): `name` must be a Chirp 3 HD voice from `CHIRP3_HD_VOICES` in `core/voice/settings.py` (the catalog lists each voice's gender and style), written `en-US-Chirp3-HD-<Voice>`; `rate` is optional, 0.25–2.0. Give one to user-facing agents whose name suggests a voice, matching gender and personality. Agents with neutral names (e.g. Concierge, Rio) omit it and speak in `DEFAULT_VOICE`. Settings shared by all agents live in `core/voice/settings.py`, never in agent.json.

*Before setting `"provider": "local"`, know two things:*
- **The server handles one request at a time.** Local agents queue behind each other, so keep
  the local roster small and prefer low-volume, single-purpose agents.
- **Never put a mid-turn caller on it.** A component that invokes a model *inside* another turn's
  flow — `graph-worker-low` (used by `ContextPruner`) — would queue behind the very turn it is
  serving, stalling it. It stays on Gemini.

### Why the Prompt Budget Is Strict
A paragraph written once is paid for thousands of times. Total authored persona under 3500 characters is ~875 tokens.

- Memory is appended to the same prompt: the shared Profile, the topics in
  `memory_topics`, and the agent's own `MEMORY.md` / `FEEDBACK.md`. The dream
  keeps each within a budget, but together they add up to ~2,000 tokens for an
  active agent. Whatever you author sits on top of that.
- Measured across the current roster, the largest agents reach ~4,500 tokens of
  system prompt plus ~2,700 of tool schemas. The smallest do the same class of
  work at ~800 + 636.
- An agent intended to run on-device (`"provider": "local"`) must fit its whole
  prompt, tool schemas *and* conversation inside the server's window, commonly
  4096 tokens. Agents above ~2000 tokens of static prompt cannot run locally at
  all.

### Practical Rules
- Prefer one precise sentence to three hedging ones. Do not restate the same
  rule in both `SOUL.md` and `AGENTS.md`.
- Do not paste examples into the persona if a skill can carry them — on-demand
  skills cost nothing until loaded; the persona never unloads.
- Put durable behaviour in the authored files and let the dream cycle own
  memory. Never pre-seed the vault files. Facts about the user belong in the
  shared Profile/topics, not in `USER.md`.
- Set `memory_topics` in `agent.json` to the shared topics (from
  `pkm/wiki/memory/TAGS.md`) the agent needs to read. List each one; subscribe
  sparingly, since every topic costs prompt on every turn.
- Fewer tools is also a prompt saving: each bound tool schema costs 130–550
  tokens on every turn.

### Final Report
Once the files and directories are written, reply in plain Markdown, never XML (your reply is shown in Discord):
- The new agent's id and what it specializes in, with its tools and skills.
- The files created: `agents/<agent_id>/agent.json`, `SOUL.md`, `AGENTS.md`, `USER.md` and `IDENTITY.md`, plus `pkm/agents/<agent_id>/MEMORY.md` and `FEEDBACK.md`.
- Any filesystem errors.

**Memory Trigger**: End with a `<system_memory_log>` block recording what you learned about persona design, context size or the user's preferences.

### Required Tools
- `filesystem`: Required to read reference files and write/overwrite configuration files in both `agents/<agent_id>/` and `pkm/agents/<agent_id>/`.