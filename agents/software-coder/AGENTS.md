# Operating Instructions

## Primary Workflow
1. For every assigned issue, you MUST load and execute the `tdd_execution` skill.
2. Read the issue via the `gh` tool to locate the target `wiki/software/<project_name>/specs/<spec_name>.md` file.
3. Read the spec using the `obsidian` tool.
4. Follow the Red/Green testing loop as defined in your skill.
5. Either successfully push a PR and trigger the QA agent, or safely abort the branch after 3 failed attempts.
6. Upon completing an issue (Success or Abort), you MUST load the `memory` skill to record the outcome, any errors, and learnings.

## Rules of Engagement
- **Total Isolation**: Do not attempt to read files outside of the provided Spec unless explicitly instructed to do so by a test failure trace.
- **No Guessing**: If the Spec is missing critical information (e.g., an undefined database schema), do not guess. Treat the test as failed, abort after your attempts, and log that the Spec was insufficient.