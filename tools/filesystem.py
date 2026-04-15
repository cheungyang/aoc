import os
from langchain_core.tools import tool
from core.loaders.agents_loader import AgentsLoader
from core.util import format_tool_response

@tool
def filesystem(agent_id: str, instructions: list[dict]) -> str:
    """
    Perform file operations (read, write, overwrite, ls, delete, rmdir) with scoped permissions.
    Supports executing multiple actions in a single call.
    
    Args:
        agent_id: The ID of the agent running this tool.
        instructions: A list of dictionaries, where each dictionary represents an action to perform.
            Each dictionary must contain:
            - 'action': The operation to perform ('read', 'write', 'overwrite', 'ls', 'delete', 'rmdir').
            - 'path': The path to the file or directory.
            - 'content': (Optional) Content to write/overwrite.
    """
    if not agent_id:
        return format_tool_response("filesystem", payload="", errors="Error: agent_id is required to verify permissions.")

    from core.loaders.tools_loader import ToolsLoader
    tools_loader = ToolsLoader()

    if len(instructions) > 10:
        return format_tool_response("filesystem", payload="", errors="Error: Too many instructions requested (limit is 10).")

    payload_elements = []
    error_elements = []

    for inst in instructions:
        action = inst.get("action")
        path = inst.get("path")
        content = inst.get("content", "")
        
        if action is None or path is None:
             error_elements.append(f'<instruction_error action="{action}" path="{path}">Error: Both \'action\' and \'path\' are required in each instruction.</instruction_error>')
             continue

        # Permission check
        if not tools_loader.check_permission(agent_id, "filesystem", action, path):
            error_elements.append(f'<instruction_error action="{action}" path="{path}">Error: Agent {agent_id} does not have permission to perform \'{action}\' on path {path}</instruction_error>')
            continue

        p, e = _execute_single_action(action, path, content)
        
        if e and e != "None":
            error_elements.append(f'<instruction_error action="{action}" path="{path}">{e}</instruction_error>')
        else:
            payload_elements.append(f'<instruction_result action="{action}" path="{path}">{p}</instruction_result>')

    full_payload = "\n".join(payload_elements)
    full_errors = "\n".join(error_elements) if error_elements else "None"
    
    return format_tool_response("filesystem", payload=full_payload, errors=full_errors)


def _execute_single_action(action: str, path: str, content: str) -> tuple[str, str]:
    try:
        if action == "read":
            if not os.path.exists(path):
                return "", f"Error: File not found at {path}"
            with open(path, "r") as f:
                return f.read(), "None"
        
        elif action == "write":
            if os.path.exists(path):
                return "", f"Error: File already exists at {path}. Use 'overwrite' if intentional."
            # Ensure parent directories exist
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
            return f"Successfully wrote to {path}", "None"
            
        elif action == "overwrite":
            # Ensure parent directories exist
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
            return f"Successfully overwrote {path}", "None"
            
        elif action == "ls":
            if not os.path.exists(path):
                return "", f"Error: Path not found at {path}"
            if not os.path.isdir(path):
                return "", f"Error: Path {path} is not a directory"
            items = os.listdir(path)
            return "\n".join(items) if items else "Directory is empty", "None"
            
        elif action == "delete":
            if not os.path.exists(path):
                return "", f"Error: File not found at {path}"
            if not os.path.isfile(path):
                return "", f"Error: Path {path} is not a file. 'delete' action only deletes single files."
            os.remove(path)
            return f"Successfully deleted file {path}", "None"
            
        elif action == "rmdir":
            if not os.path.exists(path):
                return "", f"Error: Path not found at {path}"
            if not os.path.isdir(path):
                return "", f"Error: Path {path} is not a directory"
            import shutil
            shutil.rmtree(path)
            return f"Successfully removed directory {path}", "None"
            
        else:
            return "", f"Error: Unknown action '{action}'"
            
    except Exception as e:
        return "", f"Error performing filesystem action: {e}"