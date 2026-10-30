---
name: tool_audit
description: Checks one of your own tools against its raw sources: calls it with explain=true, recomputes a sample by hand from WORKFLOW.md and RULES.md, and reports any mismatch.
---
## When to Use
The user doubts a number one of your own tools gave, asks how it was reached, or asks you to check a tool. Also after the user fixed a file that a tool reported as partial or failed.

## Workflow
1. Call the tool again with the same arguments plus `explain=true`. Note its status and issues, the files it read (modified time, records, lines skipped), and the inputs it lists behind each number.
2. In one step, read the tool's section of `agents/<agent_id>/WORKFLOW.md`, your `pkm/agents/<agent_id>/RULES.md`, and the raw files the explain block lists (one `filesystem` call holds up to 10 reads), asking for `high` thinking: recomputing by hand is the heavy step.
3. Pick a sample: the number in question plus two or three others, covering each rule the tool applied. Recompute each from the raw lines, following WORKFLOW.md's rule with RULES.md's value (a key RULES.md doesn't set uses the default WORKFLOW.md gives).
4. Check coverage too: the records and skipped lines match the file, and every line the tool called unreadable really is.
5. Report:
   - a table: number | tool | by hand | match;
   - for each mismatch: the rule, the inputs, both numbers, and the likely cause (the file changed shape, a RULES.md value, a rule WORKFLOW.md doesn't describe, or a bug).
6. Change no code. A threshold that's wrong for the user: propose the RULES.md change (old → new) and make it once they agree. Anything else: note it in your memory log as a tool gap (which tool, which file, what was wrong), so agent-designer picks it up.

## Required Tools
- Your own tool, with `explain=true`.
- `filesystem`: to read WORKFLOW.md, RULES.md and the sources your tool reads (your own read grants cover them).
