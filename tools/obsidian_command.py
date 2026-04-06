import os
import subprocess
import shlex
from fastmcp.tools import tool

@tool()
def obsidian_command(args: str) -> str:
    """
    Executes an Obsidian CLI command with the provided args.
    The CLI is located at /Applications/Obsidian.app/Contents/MacOS/obsidian.
    Example: args="commands" or args="create name='My Note' content='Hello'"
    """
    cli_path = "/Applications/Obsidian.app/Contents/MacOS/obsidian"
    
    if not os.path.exists(cli_path):
        return f"Error: Obsidian CLI not found at {cli_path}"

    try:
        # Assemble execution command
        cmd = [cli_path] + shlex.split(args)
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False
        )
        
        # Assemble response output
        output = []
        if result.stdout:
            output.append(result.stdout)
        if result.stderr:
            output.append(result.stderr)
            
        return "\n".join(output)
            
    except Exception as e:
        return f"Unexpected Error executing obsidian command: {e}"
