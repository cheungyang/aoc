import os
import subprocess
import shlex
from langchain_core.tools import tool
from core.util import format_tool_response

@tool
def git(command: str, path: str) -> str:
    """
    Execute a Git command in the specified path.
    
    Focus areas and examples:
    - Clone: clone <url>
    - Pull: pull origin <branch>
    - Add: add <file> or add .
    - Commit: commit -m "message"
    - Push: push origin <branch>
    - Branch: branch or checkout -b <name> or checkout <name>
    - Log: log --oneline or log -p <file>
    - Status: status
    
    The command argument should be the rest of the command after 'git'.
    Example: git(command="status", path="/path/to/repo")
    """
    try:
        # Split command safely
        args = shlex.split(command)
        cmd = ["git"] + args

        result = subprocess.run(
            cmd,
            cwd=path,
            capture_output=True,
            text=True,
            check=False
        )

        output = []
        if result.stdout:
            output.append(result.stdout)
        if result.stderr:
            output.append(result.stderr)

        return format_tool_response("git", payload="\n".join(output), errors="None")

    except Exception as e:
        return format_tool_response("git", payload="", errors=f"Error performing git action: {e}")

