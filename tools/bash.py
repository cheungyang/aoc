import os
import subprocess
import shlex
from langchain_core.tools import tool
from core.loaders.tools_loader import ToolsLoader
from core.util import format_tool_response

@tool
def bash(command_string: str, cwd: str, agent_id: str) -> str:
    """
    Run a bash command in a specified directory.
    Usage Guidelines:
    - Provide the full command string (e.g., "python script.py").
    - The first token of the command must be allowlisted in your skill or agent config for the given `cwd`.
    - The command is executed directly without a shell for security.
    - `cwd` must be specified and you must have permission to access it.
    """
    if not agent_id:
        return format_tool_response("bash", payload="", errors="Error: agent_id is required to verify permissions.")

    if not command_string:
        return format_tool_response("bash", payload="", errors="Error: command_string is required.")

    if not cwd:
        return format_tool_response("bash", payload="", errors="Error: cwd (current working directory) is required.")

    try:
        # Parse the command string to get the command name
        args = shlex.split(command_string)
        if not args:
            return format_tool_response("bash", payload="", errors="Error: Empty command string.")
            
        command_name = args[0]

        tools_loader = ToolsLoader()

        # Check permission
        # We pass the command_name as action_name, and cwd as path
        if not tools_loader.check_permission(agent_id, "bash", command_name, path=cwd):
            return format_tool_response("bash", payload="", errors=f"Error: Agent {agent_id} does not have permission to run '{command_name}' in {cwd}")

        # Run the command
        print(f"DEBUG: Running bash command: {args} in {cwd}")
        result = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False
        )

        output = []
        if result.stdout:
            output.append(result.stdout)
        if result.stderr:
            output.append(result.stderr)
            
        full_output = "\n".join(output)
        
        return format_tool_response("bash", payload=full_output, errors="None" if result.returncode == 0 else f"Error code {result.returncode}")

    except Exception as e:
        return format_tool_response("bash", payload="", errors=f"Error executing bash command: {e}")
