import os
import subprocess
import shlex

def git_command(args: str, path: str = ".") -> str:
    """
    Executes a git command with args in the specified folder path.
    The path should be relative to the agent's workspace root.
    Example: args="log -n 5", path="./agents/concierge"
    """
    target_dir = os.path.abspath(path)
    
    if not os.path.isdir(target_dir):
        return f"Error: Directory not found: {path} (Resolved to: {target_dir})"

    try:
        # Safely split args using shlex
        cmd_args = ["git"] + shlex.split(args)
        
        result = subprocess.run(
            cmd_args,
            cwd=target_dir,
            capture_output=True,
            text=True,
            check=False
        )
        
        output = []
        if result.stdout:
            output.append(f"Stdout:\n{result.stdout}")
        if result.stderr:
            output.append(f"Stderr:\n{result.stderr}")
            
        if result.returncode == 0:
            return "Success:\n" + "\n".join(output)
        else:
            return f"Error: git command failed with exit code {result.returncode}\n" + "\n".join(output)
            
    except Exception as e:
        return f"Unexpected Error executing git command: {e}"
