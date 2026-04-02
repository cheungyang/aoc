---
name: git
description: Perform advanced git operations (log, branch, status, revert, diff, commit, pull, push). Use when user asks for any git repository task.
---
To execute advanced git operations, use the `git_command` tool.

Guidelines:
1. **Target Path**: Always specify the `path` argument relative to the workspace root (e.g., `./agents/concierge` or `.`).
2. **Command Args**: Pass the git command arguments (excluding the "git" binary) to the `args` parameter.

Common Usage Examples:
- **Check Status**: `git_command(args="status")`
- **View Log**: `git_command(args="log -n 5")`
- **View Diff**: `git_command(args="diff")`
- **List Branches**: `git_command(args="branch -a")`
- **Pull code**: `git_command(args="pull origin main")`
- **Commit**: `git_command(args="commit -m 'your message'")`
- **Push**: `git_command(args="push origin main")`

Note: Use shlex safe arguments. The tool handle splitting.
