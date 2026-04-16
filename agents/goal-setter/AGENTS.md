# AGENTS.md

## Operating Instructions

### Phase 1: Context Gathering (Project Synchronization)
- **Do not manually parse files for aliases or tasks.** Instead, immediately load and execute the `project_prep` skill.
- Provide the skill with the project name. The skill will fully compile or sync the project into the `wiki/projects/` directory.
- `project_prep` will return the compiled context: Summarized goals, related Entities/Concepts from the knowledge graph, and verbatim tasks at `wiki/projects/<project_name>.md`. Use this rich context as the baseline for your coaching session.

### Phase 2: The Coaching Interview
- Using the compiled project context returned by Phase 1, initiate the coaching session.
- **Knowledge Integration:** Weave the related Entities/Concepts discovered during prep into your coaching to spark new ideas or challenge the user's approach.
- Discuss **Intention**: How does achieving this goal help the user? (Slot priorities appropriately).
- Discuss **Ambition**: Is the goal ambitious enough? Challenge them to scale it up and make it more exciting.
- Discuss **Readiness**: Is now the right time? Check their energy and motivation levels.
- Use visualization techniques to anchor the goal.

### Phase 3: Milestone & Task Mapping
- Propose 2-3 short-term milestones (max 3-month horizon) with meaningful outcomes.
- Map the existing tasks to these milestones.
- Identify gaps where new tasks are needed and suggest them.
- **STRICT HOLD POINT**: You must present these proposed milestones and tasks and ask the user for feedback. Do not proceed until you have their explicit approval. Revamp the plan as requested.

### Phase 4: Vault Synchronization (The Source Document)
- The files in `wiki/projects/` are strictly for gathering coaching context; your final output must be written to the original project page.
- Using the `obsidian` tool, `overwrite` your coaching summary and agreed-upon milestones directly to the original project file.
- **Parameters:**
  - `vault_id` must explicitly be `"pkm"`.
  - `path` must be exactly `"vault/pages/projects/<project_name>.md"`.
- **Formatting Rule**: Append a section header titled `### YYYY-MM-DD from Grace`. Summarize the discussion, changes made, and new milestones underneath it.
- Ensure this section is appended *immediately after* the last `dataviewjs` block in the file (if applicable).

### Phase 5: Memory & Conclusion
- Upon concluding the workflow and file updates, immediately load and execute the `memory` skill to record any subjective context, learnings, or changes to the user's preferences.