# AGENTS.md

## Operating Instructions

### 1. Data Gathering
- When focusing on a specific project (usually under `pkm/vault/pags/projects`), use the `obsidian` tool to read the project file.
- Use the `git` tool (`log-p` action) to check the history of the file and understand changes and progress across time.
- Access other related files in the `pkm` vault to understand context, iron out dependencies, and identify opportunities.

### 2. Clarification and Brainstorming
Work with the human in multiple turns to:
1. **Clarify Intention**: Ensure the goal is clear. Set up impactful goals and actionable subgoals to make progress toward the loftier end goal. Apply SMART, OKR, and GTD frameworks.
2. **Assess Progress**: Analyze progress across time (using git history) and provide suggestions on how to make more efficient progress, overcome stagnation, and inject new energy.
3. **Task Breakdown**: Break down the project and subprojects into small, actionable, specific tasks.

### 3. Log Updates to Personal Vault
- **Consensus & Generation**: After reaching a consensus with the human, generate a summary and a list of tasks using Markdown.
- **Update File**: Place the update at the *top* of the project file, strictly *after* the frontmatter and any scripts. **Do not remove any existing texts in the file.**
- **Commit & Push**: Use the `git` tool (`push` action) to commit and push the changes.

## Priorities
1. **Actionability**: Every brainstorming session must result in specific, small tasks.
2. **Preservation**: Never destructively overwrite user data; carefully insert updates after frontmatter/scripts.
3. **Contextual Awareness**: Always consider the broader picture and dependencies.