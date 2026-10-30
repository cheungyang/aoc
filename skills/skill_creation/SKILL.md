---
name: skill_creation
description: Skill for creating new skills with required guidelines and structure, including SKILL.md and skill.json. Skills hold steps and decision logic; numbers stay in RULES.md and tool facts in WORKFLOW.md.
---
## Overview
This skill guides agents on how to create a new skill in the system, including the required `SKILL.md` (instructions) and `skill.json` (metadata and permissions) files. It ends with a short Markdown report for the user.

## Boundaries & Guardrails
- **Strict Approval**: NEVER use the filesystem tool to create or overwrite skill files without first presenting a detailed plan (covering the proposed instructions, the exact tool JSON mapping, how each agent will load it: preload mode and the characters it adds, and the model calls its workflow takes) and waiting for explicit user approval.
- **Formatting**: Reply in plain Markdown, never XML: your reply is shown in Discord, even when the Concierge relays the request.

## What Goes in a Skill
A skill holds steps and decision logic: what to do, in what order, and how to choose. Everything else has its own home (the table in AGENTS.md):
- **Numbers go to RULES.md.** A threshold the user owns ("alert on a 4% move", "coach after 14 days") is a key in `pkm/agents/<agent_id>/RULES.md`. The skill names the key, never the value: "when the move passes `move_pct`", not "when it passes 4%". A number the user would never change, a safety limit, stays in code.
- **Tool facts come from WORKFLOW.md.** What a tool reads, computes and leaves out is its contract, in `agents/<agent_id>/WORKFLOW.md`, and its docstring carries the short version. The skill says when to call the tool and what to decide from its result; it never restates the tool's rules, formulas or file formats.
- **Gaps are said, not guessed.** When a tool reports `partial` or `failed`, the skill tells the agent to name what's missing in one line, work from the raw lines the tool returned, and offer the fix.
- **Code only under the code rule in AGENTS.md.** A step that needs a tool that doesn't exist is code: name it in the plan and ask for it with `workflow_change`.

## Workflow

### Prerequisite
Before triggering this skill, the agent MUST have a good understanding of the human's intention and the specific function of the new skill being created.

### Steps to Create a Skill

#### 1. Create Directory and Files
Use the `filesystem` tool's `write` action to create two files under `skills/<skill_name>/`:
1. `SKILL.md`: The instructional logic and guidelines.
2. `skill.json`: The metadata and tool permission requirements.
The `write` action automatically creates any parent directories that do not exist.

#### 2. Format SKILL.md
At the top of `SKILL.md`, include the name and a one-line description in frontmatter format:
```yaml
---
name: <skill_name>
description: <one-line description>
---
```
*The formatting is important as it will be extracted to show a skills list to the agent before loading the entire skill file.*

**Choose how agents load it.** Each agent sets this per skill in its `agent.json` `skills` object (never in `skill.json`):
- **On demand** (`{}`, the default): the agent sees only the one-line description and calls `load_skill` when needed. Use it for rarely used or long workflows (weekly reviews, setup flows, authoring).
- **Full** (`{"preload": "full"}`): the whole body, minus frontmatter, is in the agent's system prompt every turn. Use it for a short skill the agent runs on nearly every turn or run. Make it self-contained: an agent whose skills are all `full` has no `load_skill` tool.
- **Core** (`{"preload": "core"}`): only the Core section is in the prompt every turn; `load_skill` still returns the whole file. Use it for a longer skill the agent uses often but whose schemas, tables or examples it rarely needs.

**Preload budget:** an agent's preloaded text (full bodies without frontmatter, plus Core sections) must stay within 6,000 characters (~1.5k tokens); a test enforces it. Measure the new skill's contribution and add it to each agent's existing preloads before choosing `full` or `core`.

**Core/Reference format** (required for `core`):
- After the frontmatter, a line `## Core`; later, a line `## Reference`. The loader inlines exactly the text between those two lines.
- Inside both sections use `###` or deeper for sub-headings; no other `##` headings in the file.
- Core holds what the agent needs every time it runs the workflow (guardrails, steps, output contract). Reference holds schemas, lookup tables, examples and rarely needed detail.
- End Core with one line saying when to call `load_skill("<skill_name>")` for the Reference.

**Heavy skill:** when the workflow needs more depth than the agent's quick turns (a plan, a choice under several constraints, checking numbers by hand), give `skill.json` a `thinking` level, e.g. `"thinking": "high"`. Once a turn loads the skill, the rest of the turn runs at that level (voice turns cap at medium), with no step asking for it. A preloaded skill isn't loaded, so also name the tool that starts its workflow, one the skill grants: `"thinking": {"level": "medium", "after": ["workout_setup"]}`. Or, for a Core preload, have the heavy step call `load_skill` along with its reads, as `agent_creation` does.

**Required Tools Section:**
At the very end of the `SKILL.md` file, you MUST include a `## Required Tools` heading (`### Required Tools`, as the last part of Reference, in a Core/Reference skill). List out each tool the skill requires and a brief explanation of why it is needed to perform the skill.

#### 3. Format skill.json
Create the `skill.json` file to explicitly declare the metadata and tool permissions required for this skill. Use the following JSON structure template:
```json
{
  "skill_id": "<skill_name>",
  "name": "<Human Readable Name>",
  "description": "<One-line description>",
  "emoji": "<Appropriate Emoji>",
  "tools": {
    "<tool_name>": {
      "<resource_or_path>": [
        "<action or @bundle>"
      ]
    }
  }
}
```
*Critical Constraint:* The tools listed in the Required Tools section of `SKILL.md` MUST perfectly align with the tools and permission payloads defined in the `tools` object of `skill.json`.
*Grants:* Use `@` bundles (`filesystem`: `@read`, `@write`, `@manage`), adding bare actions only to grant less than a bundle, e.g. `["@read", "append"]`. Grant a read of every source the skill's tools read. The `update_permissions` skill has the bundle rules.

#### 4. Check
Run `agent_lint` on each agent that uses the skill and fix what it finds before reporting done.

#### 5. Final Report & Memory
Once the files are written, reply in plain Markdown: the skill's name and purpose, the files created (`skills/<skill_name>/SKILL.md` and `skill.json`), the tools it grants, any `agent_lint` finding you left in place with why, and any filesystem errors.
**Memory Trigger**: End with a `<system_memory_log>` block recording what you learned about prompt architecture, behavioral boundaries, or the user's preferences for skill design.

### How to Make a Good Skill
- **Concise Description**: The description must clearly state the skill's purpose so the agent can quickly determine relevance.
- **Actionable Instructions**: Use clear, unambiguous steps.
- **Focus on Workflows**: Focus on workflows rather than just features.
- **Clear Boundaries & Guardrails**: Define when to use the skill and, importantly, when not to use it, including limits on loops (e.g., maximum web searches).
- **Examples of Success**: Include clear example inputs and expected outputs (e.g., "If user asks X, do Y") to guide the AI. In a Core/Reference skill, put them in Reference.
- **Focus on Modularity**: Create separate, specialized skill files rather than one large, complex file.
- **One Owner per Rule**: The tool's docstring owns how to call it, WORKFLOW.md owns what it computes, RULES.md owns the numbers, the skill owns its steps and decisions, and the agent's `AGENTS.md` owns how the agent moves between workflows. Never copy one into another.
- **Few Round Trips**: Walk the workflow through the Round-trip Budget checklist in `agent_creation`. A fixed read or a sum the model would redo every run is a tool's job, under the code rule.
- **Markdown Replies**: A skill's reply is shown in Discord, so its output format is Markdown, never an XML block. XML is only for machine readers: graph worker hand-offs and files another agent parses.

## Required Tools
- `filesystem`: Required to read, write, and overwrite `SKILL.md` and `skill.json` files within the `skills/` directory, and to read agents' files (`agent.json`, WORKFLOW.md) for preload budgets and tool facts.
- `agent_lint`: to check each agent that uses the skill.