import os
import subprocess
import shlex
from fastmcp import FastMCP

# Initialize the server
mcp = FastMCP("Concierge Tool Server")

@mcp.tool
def git_command(args: str, path: str = ".") -> str:
    """
    Executes a git command with args in the specified folder path.
    The path should be relative to the agent's workspace root.
    Example: args="log -n 5", path="./agents/concierge"
    """
    # Build path to the script bundled in skills
    script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "skills", "git", "scripts", "git.py"))
    
    if not os.path.exists(script_path):
        return f"Error: Tool implementation script not found at {script_path}"

    try:
        # Assemble execution command
        cmd = ["python", script_path, path] + shlex.split(args)
        
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
        return f"Unexpected Error executing logic script wrapper: {e}"

if __name__ == "__main__":
    mcp.run()
