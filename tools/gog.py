import os
import subprocess
import shlex
from langchain_core.tools import tool
from core.loaders.agents_loader import AgentsLoader

@tool
def gog(command: str) -> str:
    """
    Execute a gog calendar command using gogcli.
    Example commands:
    - calendar calendars
    - calendar events primary --today
    - calendar create primary --summary 'Meeting' --from 2026-04-07T10:00:00Z --to 2026-04-07T11:00:00Z
    """
    # Resolve path to gog binary
    workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    gog_bin = os.path.join(workspace_root, "bin", "gog")

    if not os.path.exists(gog_bin):
        return f"Error: gog binary not found at {gog_bin}. Please ensure it is installed."

    try:
        # Split command safely
        args = shlex.split(command)
        cmd = [gog_bin] + args

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

        return "\n".join(output)

    except Exception as e:
        return f"Error performing gog action: {e}"
