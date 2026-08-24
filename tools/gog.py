import os
import subprocess
import shlex
from langchain_core.tools import tool
from core.loaders.agents_loader import AgentsLoader
from core.util import format_tool_response

@tool
def gog(command: str) -> str:
    """
    Execute a gog CLI command using gogcli (supports Google Calendar and Gmail).

    The command argument should be the rest of the command after 'gog'.

    Example commands:
    # Calendar operations:
    - calendar calendars (List all calendars)
    - calendar events primary --today (List events on primary calendar)
    
    # To add events to a specific calendar, replace 'primary' with the calendar name or ID:
    - calendar create 'Work Calendar' --summary 'Meeting' --from 2026-04-07T10:00:00Z --to 2026-04-07T11:00:00Z
    
    # To set event color, use the --color flag (Google Calendar uses color IDs 1-11):
    - calendar create primary --summary 'Meeting' --color 1 --from 2026-04-07T10:00:00Z --to 2026-04-07T11:00:00Z

    # Gmail operations:
    - gmail search 'newer_than:7d' --max 10 --json (Search emails matching query)
    - gmail get <messageId> --sanitize-content --json (Get email message content by ID)
    - gmail thread get <threadId> --sanitize-content --json (Get email thread content by ID)
    - gmail settings filters export | list | create | delete (Manage Gmail filters)
    """
    # Resolve path to gog binary
    workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    gog_bin_workspace = os.path.join(workspace_root, "bin", "gog")
    gog_bin_usr_local = "/usr/local/bin/gog"

    if os.path.exists(gog_bin_workspace):
        gog_bin = gog_bin_workspace
    elif os.path.exists(gog_bin_usr_local):
        gog_bin = gog_bin_usr_local
    else:
        return format_tool_response("gog", payload="", errors=f"Error: gog binary not found at {gog_bin_workspace} or {gog_bin_usr_local}. Please ensure it is installed.")

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

        return format_tool_response("gog", payload="\n".join(output), errors="None")

    except Exception as e:
        return format_tool_response("gog", payload="", errors=f"Error performing gog action: {e}")
