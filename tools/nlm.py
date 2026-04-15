import os
import subprocess
import shlex
from langchain_core.tools import tool
from core.util import format_tool_response

@tool
def nlm(command: str) -> str:
    """
    Execute a notebooklm-cli command using nlm.
    Example commands:
    - notebook list
    - notebook create "My Notebook"
    - source add <notebook_id> --url "https://example.com"
    """
    # Resolve path to nlm binary
    workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    nlm_bin = os.path.join(workspace_root, "bin", "nlm")

    if not os.path.exists(nlm_bin):
        return format_tool_response("nlm", payload="", errors=f"Error: nlm binary not found at {nlm_bin}. Please ensure it is installed.")

    try:
        # Split command safely
        args = shlex.split(command)
        cmd = [nlm_bin] + args

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

        return format_tool_response("nlm", payload="\n".join(output), errors="None")

    except Exception as e:
        return format_tool_response("nlm", payload="", errors=f"Error performing nlm action: {e}")
