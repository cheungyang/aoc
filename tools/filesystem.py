import os
from fastmcp.tools import tool
from core.agent.agents_loader import AgentsLoader

@tool()
def filesystem(action: str, path: str, content: str = "", agent_id: str = "") -> str:
    """
    Perform file operations (read, write, overwrite) with scoped permissions.
    The 'write' action fails if the file already exists.
    The 'overwrite' action proceeds even if the file exists.
    Both write and overwrite automatically create parent directories if they don't exist.
    Permissions are checked against the agent's allowlist in agent.json.
    """
    if not agent_id:
        return "Error: agent_id is required to verify permissions."

    # Load agent config
    loader = AgentsLoader()
    config = loader.get_agent_config(agent_id)
    if not config:
        return f"Error: Configuration not found for agent {agent_id}"

    # Get filesystem permissions from root of agent.json
    permissions = config.get("tools", {}).get("filesystem", {})

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
        if action == "read":
            if not os.path.exists(target_path):
                return f"Error: File not found at {path}"
            with open(target_path, "r") as f:
                return f.read()
        
        elif action == "write":
            if os.path.exists(target_path):
                return f"Error: File already exists at {path}. Use 'overwrite' if intentional."
            # Ensure parent directories exist
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with open(target_path, "w") as f:
                f.write(content)
            return f"Successfully wrote to {path}"
            
        elif action == "overwrite":
            # Ensure parent directories exist
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with open(target_path, "w") as f:
                f.write(content)
            return f"Successfully overwrote {path}"
            
        else:
            return f"Error: Unknown action '{action}'"
            
    except Exception as e:
        return f"Error performing filesystem action: {e}"
