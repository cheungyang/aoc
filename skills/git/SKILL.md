---
name: git
description: Perform git operations (pull, push, log) with scoped permissions. Use when user asks for any git repository task.
---
## Available scripts
- **`tools/git.py`** — Executes git operations (pull, push, log) with scoped permissions.

**Command:**
`git(action="pull", path="pkm-oc", agent_id="<agent_id>")`

## Workflow
To execute git operations, use the `git` tool. Permissions are checked against the agent's allowlist in `agent.json`.

Guidelines:
1. **Action**: Choose from `pull`, `push`, `log`.
2. **Path**: Specify the path where the operation should be performed.
3. **Agent ID**: Pass your `agent_id` to verify permissions.
4. **Message**: Required for `push` action (used as commit message).

Common Usage Examples:
- **Check Log**: `git(action="log", path="pkm-oc", agent_id="agent-designer")`
- **Pull Code**: `git(action="pull", path="pkm-oc", agent_id="agent-designer")`
- **Push Code**: `git(action="push", path="pkm-oc", agent_id="agent-designer", message="feat: add new feature")`
