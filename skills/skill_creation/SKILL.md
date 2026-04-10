---
name: skill_creation
description: Skill for creating new skills with required guidelines and structure, including SKILL.md and skill.json.
---
## Overview
This skill guides agents on how to create a new skill in the system, including the required `SKILL.md` (instructions) and `skill.json` (metadata and permissions) files.

## Boundaries & Guardrails
- **Strict Approval**: NEVER use the filesystem tool to create or overwrite skill files without first presenting a detailed plan (covering the proposed instructions and the exact tool JSON mapping) and waiting for explicit user approval.

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

**Required Tools Section:**
At the very end of the `SKILL.md` file, you MUST include a `## Required Tools` heading. List out each tool the skill requires and a brief explanation of why it is needed to perform the skill.

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
*Critical Constraint:* The tools listed in the `## Required Tools` section of `SKILL.md` MUST perfectly align with the tools and permission payloads defined in the `tools` object of `skill.json`.

### How to Make a Good Skill
- **Concise Description**: The description must clearly state the skill's purpose so the agent can quickly determine relevance.
- **Actionable Instructions**: Use clear, unambiguous steps.
- **Focus on Workflows**: Focus on workflows rather than just features.
- **Clear Boundaries & Guardrails**: Define when to use the skill and, importantly, when not to use it, including limits on loops (e.g., maximum web searches).
- **Examples of Success**: Include clear example inputs and expected outputs (e.g., "If user asks X, do Y") to guide the AI.
- **Focus on Modularity**: Create separate, specialized skill files rather than one large, complex file.

## Required Tools
- `filesystem`: Required to read, write, and overwrite `SKILL.md` and `skill.json` files within the `skills/` directory.