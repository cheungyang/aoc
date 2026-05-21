import os
import subprocess
import shlex
from langchain_core.tools import tool
from core.loaders.tools_loader import ToolsLoader
from core.util import format_tool_response

@tool
def gh(action: str, agent_id: str, task_description: str = "", session_id: str = "", flags: str = "") -> str:
    """
    Perform GitHub agent-task operations (create, view).
    - create: Create an agent task. Requires task_description.
    - view: View an agent task. Requires session_id or PR reference.
    Permissions are checked against the agent's allowlist in agent.json or skill.json.
    """
    if not agent_id:
        return format_tool_response("gh", payload="", errors="Error: agent_id is required to verify permissions.")

    tools_loader = ToolsLoader()

    # We use "agent-task" as the 'path' to match the key in skill.json
    resource = "agent-task"
    
    if not tools_loader.check_permission(agent_id, "gh", action, path=resource):
        return format_tool_response("gh", payload="", errors=f"Error: Agent {agent_id} does not have permission to perform '{action}' on '{resource}'")

    try:
        def run_gh_cmd(cmd_args):
            cmd = ["gh"] + cmd_args
            print(f"DEBUG: Running gh cmd: {cmd}")
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
            return "\n".join(output), result.returncode

        if action == "create":
            if not task_description:
                return format_tool_response("gh", payload="", errors="Error: task_description is required for create action.")
            
            cmd_args = ["agent-task", "create", task_description]
            if flags:
                cmd_args.extend(shlex.split(flags))
                
            output, code = run_gh_cmd(cmd_args)
            return format_tool_response("gh", payload=f"Create task result:\n{output}", errors="None" if code == 0 else f"Error code {code}")
            
        elif action == "view":
            if not session_id:
                return format_tool_response("gh", payload="", errors="Error: session_id (or PR ref) is required for view action.")
            
            cmd_args = ["agent-task", "view", session_id]
            if flags:
                cmd_args.extend(shlex.split(flags))
                
            output, code = run_gh_cmd(cmd_args)
            return format_tool_response("gh", payload=f"View task result:\n{output}", errors="None" if code == 0 else f"Error code {code}")
            
        else:
            return format_tool_response("gh", payload="", errors=f"Error: Unknown action '{action}'")
            
    except Exception as e:
        return format_tool_response("gh", payload="", errors=f"Error performing gh action: {e}")
