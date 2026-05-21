import os
import subprocess
import shlex
from langchain_core.tools import tool
from core.util import format_tool_response

@tool
def gh(command: str) -> str:
    """
    Execute a GitHub CLI command.
    Reference: https://cli.github.com/manual/

    Focus areas and examples:
    - Create issue: issue create --title "Title" --body "Body"
    - View issues: issue list or issue view <number>
    - Assign and unassign issue: issue edit <number> --add-assignee <user> or --remove-assignee <user>
    - Add and remove labels: issue edit <number> --add-label <label> or --remove-label <label>
    - Add comments: issue comment <number> --body "Comment"
    - Status change: issue close <number> or issue reopen <number>
    - PR approval and PR merge: pr review <number> --approve or pr merge <number>

    The command argument should be the rest of the command after 'gh'.
    Example: gh("issue list")
    """
    try:
        # Split command safely
        args = shlex.split(command)
        cmd = ["gh"] + args

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False
        )

        output = []
        if result.stdout:
            output.append(result.stdout)
        if result.stderr:
            output.append(result.stderr)

        return format_tool_response("gh", payload="\n".join(output), errors="None")

    except Exception as e:
        return format_tool_response("gh", payload="", errors=f"Error performing gh action: {e}")

