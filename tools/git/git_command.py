import os
import subprocess
import shlex

def git_command(args: str, path: str = ".") -> str:
    """
    Executes a git command with args in the specified folder path.
    The path should be relative to the agent's workspace root.
    Example: args="log -n 5", path="./agents/concierge"
    """
    workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    target_dir = os.path.abspath(os.path.join(workspace_root, path))
    
    if not target_dir.startswith(workspace_root):
         return "Error: Path traversal access denied."

    try:
        # Assemble execution command
        cmd = ["git"] + shlex.split(args)
        
        result = subprocess.run(
            cmd,
            cwd=target_dir,
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
        return f"Unexpected Error executing git directly: {e}"
