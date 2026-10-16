---
name: skill_creation
description: Skill for creating new skills with required guidelines and structure, ending with a Markdown report.
---
## Overview
This skill guides agents on how to create a new skill in the system, including the required `SKILL.md` (instructions) and `skill.json` (metadata and permissions) files. It ends with a short Markdown report for the user.

## Boundaries & Guardrails
- **Strict Approval**: NEVER use the filesystem tool to create or overwrite skill files without first presenting a detailed plan (covering the proposed instructions, the exact tool JSON mapping, and how each agent will load it: preload mode and the characters it adds) and waiting for explicit user approval.
- **Formatting**: Reply in plain Markdown, never XML: your reply is shown in Discord, even when the Concierge relays the request.

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
*Grants:* Use `@` bundles (`filesystem`: `@read`, `@write`, `@manage`), adding bare actions only to grant less than a bundle, e.g. `["@read", "append"]`. The `update_permissions` skill has the bundle rules.

#### 4. Final Report & Memory
Once the files are written, reply in plain Markdown: the skill's name and purpose, the files created (`skills/<skill_name>/SKILL.md` and `skill.json`), the tools it grants, and any filesystem errors.
**Memory Trigger**: End with a `<system_memory_log>` block recording what you learned about prompt architecture, behavioral boundaries, or the user's preferences for skill design.

### How to Make a Good Skill
- **Concise Description**: The description must clearly state the skill's purpose so the agent can quickly determine relevance.
- **Actionable Instructions**: Use clear, unambiguous steps.
- **Focus on Workflows**: Focus on workflows rather than just features.
- **Clear Boundaries & Guardrails**: Define when to use the skill and, importantly, when not to use it, including limits on loops (e.g., maximum web searches).
- **Examples of Success**: Include clear example inputs and expected outputs (e.g., "If user asks X, do Y") to guide the AI. In a Core/Reference skill, put them in Reference.
- **Focus on Modularity**: Create separate, specialized skill files rather than one large, complex file.
- **One Owner per Rule**: The tool's docstring owns tool usage (never copy it into the skill), the skill owns its workflow rules, and the agent's `AGENTS.md` owns how the agent moves between workflows.
- **Markdown Replies**: A skill's reply is shown in Discord, so its output format is Markdown, never an XML block. XML is only for machine readers: graph worker hand-offs and files another agent parses.

## Required Tools
- `filesystem`: Required to read, write, and overwrite `SKILL.md` and `skill.json` files within the `skills/` directory.