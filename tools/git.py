import os
import subprocess
import shlex
from langchain_core.tools import tool
from core.loaders.agents_loader import AgentsLoader

@tool
def git(action: str, path: str, agent_id: str, message: str = "") -> str:
    """
    Perform git operations (pull, push, log-p) with scoped permissions.
    - pull: git pull -X theirs (merges and prefers remote on conflict)
    - push: git pull -X theirs, git commit -am '<message>', git push
    - log-p: git log -p <filename>
    Permissions are checked against the agent's allowlist in agent.json.
    """
    if not agent_id:
        return "Error: agent_id is required to verify permissions."

    from core.loaders.tools_loader import ToolsLoader
    tools_loader = ToolsLoader()

    # Centralized validation for scoped tool
    if not tools_loader.check_permission(agent_id, "git", action, path):
        return f"Error: Agent {agent_id} does not have permission to perform '{action}' on path {path}"

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
            
        elif action == "log-p":
            if os.path.isfile(target_path):
                output, code = run_git_cmd(["log", "-p", os.path.basename(target_path)], os.path.dirname(target_path))
            else:
                output, code = run_git_cmd(["log", "-p"], target_path)
            return f"Log result:\n{output}"
            
        else:
            return f"Error: Unknown action '{action}'"
            
    except Exception as e:
        return f"Error performing git action: {e}"
