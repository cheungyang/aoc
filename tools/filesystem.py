import os
from langchain_core.tools import tool
from core.loaders.agents_loader import AgentsLoader

@tool
def filesystem(action: str, path: str, content: str = "", agent_id: str = "") -> str:
    """
    Perform file operations (read, write, overwrite, ls, delete, rmdir) with scoped permissions.
    The 'write' action fails if the file already exists.
    The 'overwrite' action proceeds even if the file exists.
    Both write and overwrite automatically create parent directories if they don't exist.
    Permissions are checked against the agent's allowlist in agent.json.
    """
    if not agent_id:
        return "Error: agent_id is required to verify permissions."

    from core.loaders.tools_loader import ToolsLoader
    tools_loader = ToolsLoader()

    # Centralized validation for scoped tool
    if not tools_loader.check_permission(agent_id, "filesystem", action, path):
        return f"Error: Agent {agent_id} does not have permission to perform '{action}' on path {path}"

    try:
        if action == "read":
            if not os.path.exists(path):
                return f"Error: File not found at {path}"
            with open(path, "r") as f:
                return f.read()
        
        elif action == "write":
            if os.path.exists(path):
                return f"Error: File already exists at {path}. Use 'overwrite' if intentional."
            # Ensure parent directories exist
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
            return f"Successfully wrote to {path}"
            
        elif action == "overwrite":
            # Ensure parent directories exist
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
            return f"Successfully overwrote {path}"
            
        elif action == "ls":
            if not os.path.exists(path):
                return f"Error: Path not found at {path}"
            if not os.path.isdir(path):
                return f"Error: Path {path} is not a directory"
            items = os.listdir(path)
            return "\n".join(items) if items else "Directory is empty"
            
        elif action == "delete":
            if not os.path.exists(path):
                return f"Error: File not found at {path}"
            if not os.path.isfile(path):
                return f"Error: Path {path} is not a file. 'delete' action only deletes single files."
            os.remove(path)
            return f"Successfully deleted file {path}"
            
        elif action == "rmdir":
            if not os.path.exists(path):
                return f"Error: Path not found at {path}"
            if not os.path.isdir(path):
                return f"Error: Path {path} is not a directory"
            import shutil
            shutil.rmtree(path)
            return f"Successfully removed directory {path}"
            
        else:
            return f"Error: Unknown action '{action}'"
            
    except Exception as e:
        return f"Error performing filesystem action: {e}"
