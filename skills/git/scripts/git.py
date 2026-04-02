#!/usr/bin/env python3
import sys
import os
import subprocess
import shlex

def print_help():
    print("""
Usage: python git.py <workspace_relative_path> <git_command_args>...

Executes a git command in the specified directory.

Arguments:
  workspace_relative_path  Path to the repository relative to workspace root.
  git_command_args         The arguments to pass to git (e.g. "status", "log -n 5").

Example:
  python git.py . status
  python git.py agents/concierge log -n 5
""")

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ["-h", "--help"]:
        print_help()
        sys.exit(0)
        
    if len(sys.argv) < 3:
        print("Error: Git command args are required.")
        print_help()
        sys.exit(1)
        
    path = sys.argv[1]
    git_args = sys.argv[2:]
    
    target_dir = os.path.abspath(path)
    if not os.path.isdir(target_dir):
        print(f"Error: Directory not found: {path} (Resolved to: {target_dir})")
        sys.exit(1)
        
    try:
        cmd = ["git"] + git_args
        result = subprocess.run(
            cmd,
            cwd=target_dir,
            capture_output=True,
            text=True,
            check=False
        )
        
        # Output results
        if result.returncode == 0:
             print("Success:")
        else:
             print(f"Error: Exit code {result.returncode}")
             
        if result.stdout:
            print(f"Stdout:\n{result.stdout}")
        if result.stderr:
            print(f"Stderr:\n{result.stderr}")
            
        sys.exit(result.returncode)
        
    except Exception as e:
        print(f"Unexpected Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
