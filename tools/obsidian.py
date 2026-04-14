import os
from langchain_core.tools import tool
from core.loaders.agents_loader import AgentsLoader


@tool
def obsidian(action: str, vault_id: str, agent_id: str, path: str, content: str = "", search_term: str = "") -> str:
    """
    Perform specific operations on Obsidian vaults with scoped permissions.
    Actions: file_search, read, write, overwrite, append, delete.
    """
    if not agent_id:
        return "Error: agent_id is required to verify permissions."

    from core.loaders.tools_loader import ToolsLoader
    tools_loader = ToolsLoader()

    # Resolve vault path (assuming it's in the workspace root)
    workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    vault_path = os.path.abspath(os.path.join(workspace_root, vault_id))
    
    if not os.path.exists(vault_path):
        return f"Error: Vault path not found: {vault_path}"

    # Normalize target path
    target_path = os.path.abspath(os.path.join(vault_path, path))
    
    # Check if target_path is within vault_path
    if not target_path.startswith(vault_path):
        return f"Error: Path {path} is outside of vault {vault_id}"

    if not tools_loader.check_permission(agent_id, "obsidian", action, vault_id=vault_id, path=target_path):
        return f"Error: Agent {agent_id} does not have permission to perform '{action}' on path {path}"

    try:
        if action == "read":
            if not os.path.exists(target_path):
                return f"Error: File not found at {path}"
            with open(target_path, "r") as f:
                return f.read()
        
        elif action == "write":
            if os.path.exists(target_path):
                return f"Error: File already exists at {path}. Use 'overwrite' if intentional."
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with open(target_path, "w") as f:
                f.write(content)
            return f"Successfully wrote to {path}"
            
        elif action == "overwrite":
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with open(target_path, "w") as f:
                f.write(content)
            return f"Successfully overwrote {path}"
            
        elif action == "append":
            if not os.path.exists(target_path):
                 return f"Error: File not found at {path} to append."
            with open(target_path, "a") as f:
                f.write(content)
            return f"Successfully appended to {path}"
            
        elif action == "delete":
            if not os.path.exists(target_path):
                return f"Error: File not found at {path}"
            if not os.path.isfile(target_path):
                return f"Error: Path {path} is not a file. 'delete' action only deletes single files."
            os.remove(target_path)
            return f"Successfully deleted file {path}"
            
        elif action == "file_search":
            # "list all files in path, filtered the search term"
            search_dir = target_path if os.path.isdir(target_path) else os.path.dirname(target_path)
            if not os.path.exists(search_dir):
                return f"Error: Search directory not found: {search_dir}"
                
            results = []
            search_term = search_term.lower() if search_term else ""
            
            for root, dirs, files in os.walk(search_dir):
                for file in files:
                    if search_term in file.lower() or not search_term:
                         full_p = os.path.join(root, file)
                         rel_p = os.path.relpath(full_p, vault_path)
                         results.append(rel_p)
                         
            total_results = len(results)
            N = 50
            
            if not search_term and total_results > N:
                return f"Error: Too many results ({total_results}). Please provide a search term to narrow down."
                
            if search_term:
                truncated_results = results[:N]
                output = "\n".join(truncated_results)
                output += f"\nshow {len(truncated_results)} out of {total_results} results"
                return output
            else:
                return "\n".join(results)
                
            
        else:
            return f"Error: Unknown action '{action}'"
            
    except Exception as e:
        return f"Error performing obsidian action: {e}"
