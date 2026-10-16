---
name: skill_creation
description: Skill for creating new skills with required guidelines and structure, outputting standardized IPC XML.
---
## Overview
This skill guides agents on how to create a new skill in the system, including the required `SKILL.md` (instructions) and `skill.json` (metadata and permissions) files. It concludes by outputting a standardized Inter-Process Communication (IPC) XML block.

## Boundaries & Guardrails
- **Strict Approval**: NEVER use the filesystem tool to create or overwrite skill files without first presenting a detailed plan (covering the proposed instructions, the exact tool JSON mapping, and how each agent will load it: preload mode and the characters it adds) and waiting for explicit user approval.
- **Formatting**: The final output MUST strictly adhere to the requested IPC XML structure.

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
        "<permission_scopes>"
      ]
    }
  }
}
```
*Critical Constraint:* The tools listed in the Required Tools section of `SKILL.md` MUST perfectly align with the tools and permission payloads defined in the `tools` object of `skill.json`.

#### 4. Agent-Friendly Output & Memory (IPC Format)
Once the files are successfully written, finalize the execution by outputting the strict XML structure below. This format is required to ensure perfect readability for routing agents.
```xml
<skill_creation_response>
  <original_request>[The user's request for the new skill]</original_request>
  <triggering_agent>[Agent ID or 'User']</triggering_agent>
  <payload>
    <skill_name>[Name of the generated skill]</skill_name>
    <files_created>
      - skills/<skill_name>/SKILL.md
      - skills/<skill_name>/skill.json
    </files_created>
    <tools_assigned>[List of tools mapped in the JSON]</tools_assigned>
  </payload>
  <errors>[Any filesystem errors encountered, or 'None']</errors>
  <learnings>[Execution insights on prompt architecture, behavioral boundary definitions, or user preferences for skill design]</learnings>
</skill_creation_response>
```
**Memory Trigger**: Immediately after outputting the XML, append a `<system_memory_log>` block recording the contents of the `<learnings>` tag so the system learns from this execution.

### How to Make a Good Skill
- **Concise Description**: The description must clearly state the skill's purpose so the agent can quickly determine relevance.
- **Actionable Instructions**: Use clear, unambiguous steps.
- **Focus on Workflows**: Focus on workflows rather than just features.
- **Clear Boundaries & Guardrails**: Define when to use the skill and, importantly, when not to use it, including limits on loops (e.g., maximum web searches).
- **Examples of Success**: Include clear example inputs and expected outputs (e.g., "If user asks X, do Y") to guide the AI. In a Core/Reference skill, put them in Reference.
- **Focus on Modularity**: Create separate, specialized skill files rather than one large, complex file.
- **One Owner per Rule**: The tool's docstring owns tool usage (never copy it into the skill), the skill owns its workflow rules, and the agent's `AGENTS.md` owns how the agent moves between workflows.

## Required Tools
- `filesystem`: Required to read, write, and overwrite `SKILL.md` and `skill.json` files within the `skills/` directory.