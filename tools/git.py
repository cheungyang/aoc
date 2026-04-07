import os
import subprocess
import shlex
from langchain_core.tools import tool
from core.agent.agents_loader import AgentsLoader

@tool
def git(action: str, path: str, agent_id: str, message: str = "") -> str:
    """
    Perform git operations (pull, push, log) with scoped permissions.
    - pull: git pull -X theirs (merges and prefers remote on conflict)
    - push: git pull -X theirs, git commit -am '<message>', git push
    - log: git log -n 5
    Permissions are checked against the agent's allowlist in agent.json.
    """
    if not agent_id:
        return "Error: agent_id is required to verify permissions."

    # Load agent config
    loader = AgentsLoader()
    config = loader.get_agent_config(agent_id)
    if not config:
        return f"Error: Configuration not found for agent {agent_id}"

    # Get git permissions from tools section
    permissions = config.get("tools", {}).get("git", {})

    # Resolve absolute path to check against base paths
    workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    
    # Normalize path
    target_path = os.path.abspath(path)

    allowed = False
    allowed_actions = []
    for base_path, actions in permissions.items():
        base_abs_path = os.path.abspath(os.path.join(workspace_root, base_path))
        if target_path.startswith(base_abs_path):
            allowed = True
            allowed_actions = actions
            break # Found a matching base path

    if not allowed:
        return f"Error: Agent {agent_id} does not have permission to access path {path}"

    if action not in allowed_actions:
        return f"Error: Agent {agent_id} does not have permission to perform '{action}' on path {path}. Allowed: {allowed_actions}"

    try:
        def run_git_cmd(cmd_args, cwd):
            cmd = ["git"] + cmd_args
            result = subprocess.run(
                cmd,
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
            return "\n".join(output), result.returncode

        if action == "pull":
            output, code = run_git_cmd(["pull", "-X", "theirs"], target_path)
            return f"Pull result:\n{output}"
            
        elif action == "push":
            if not message:
                return "Error: message is required for push action (for commit)."
            
            # 1. git pull -X theirs
            pull_output, pull_code = run_git_cmd(["pull", "-X", "theirs"], target_path)
            
            # 2. git commit -am '<message>'
            commit_output, commit_code = run_git_cmd(["commit", "-am", message], target_path)
            
            # 3. git push
            push_output, push_code = run_git_cmd(["push"], target_path)
            
            return f"Push process result:\nPull:\n{pull_output}\nCommit:\n{commit_output}\nPush:\n{push_output}"
            
        elif action == "log":
            output, code = run_git_cmd(["log", "-n", "5"], target_path)
            return f"Log result:\n{output}"
            
        else:
            return f"Error: Unknown action '{action}'"
            
    except Exception as e:
        return f"Error performing git action: {e}"
