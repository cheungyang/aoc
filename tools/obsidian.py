import os
from langchain_core.tools import tool
from core.loaders.agents_loader import AgentsLoader
from core.util import format_tool_response


@tool
def obsidian(agent_id: str, vault_id: str, instructions: list[dict]) -> str:
    """
    Perform specific operations on Obsidian vaults with scoped permissions.
    Supports executing multiple actions in a single call.
    
    Args:
        agent_id: The ID of the agent running this tool.
        vault_id: The ID of the vault to operate on.
        instructions: A list of dictionaries, where each dictionary represents an action to perform.
            Each dictionary must contain:
            - 'action': The operation to perform ('file_search', 'read', 'write', 'overwrite', 'append', 'delete').
            - 'path': The relative path to vault
            - 'content_or_search_term': (Optional) Content to write/append, or term to search for.
    """
    if not agent_id:
        return format_tool_response("obsidian", payload="", errors="Error: agent_id is required to verify permissions.")

    from core.loaders.tools_loader import ToolsLoader
    tools_loader = ToolsLoader()

    # Resolve vault path (assuming it's in the workspace root)
    workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    vault_path = os.path.abspath(os.path.join(workspace_root, vault_id))
    
    if not os.path.exists(vault_path):
        return format_tool_response("obsidian", payload="", errors=f"Error: Vault path not found: {vault_path}")

    if len(instructions) > 10:
        return format_tool_response("obsidian", payload="", errors="Error: Too many instructions requested (limit is 10).")

    payload_elements = []
    error_elements = []

    for inst in instructions:
        action = inst.get("action")
        path = inst.get("path")
        content_or_search_term = inst.get("content_or_search_term", "")
        
        if action is None or path is None:
             error_elements.append(f'<instruction_error action="{action}" path="{path}">Error: Both \'action\' and \'path\' are required in each instruction.</instruction_error>')
             continue

        # Map content_or_search_term based on action
        content = ""
        search_term = ""
        if action in ["write", "overwrite", "append"]:
            content = content_or_search_term
        elif action == "file_search":
            search_term = content_or_search_term

        # Normalize target path
        target_path = os.path.abspath(os.path.join(vault_path, path))
        
        # Check if target_path is within vault_path
        if not target_path.startswith(vault_path):
            error_elements.append(f'<instruction_error action="{action}" path="{path}">Error: Path is outside of vault {vault_id}</instruction_error>')
            continue

        if not tools_loader.check_permission(agent_id, "obsidian", action, vault_id=vault_id, path=target_path):
            error_elements.append(f'<instruction_error action="{action}" path="{path}">Error: Agent {agent_id} does not have permission to perform \'{action}\' on path {path}</instruction_error>')
            continue

        p, e = _execute_single_action(action, target_path, path, content, search_term, vault_path)
        
        if e and e != "None":
            error_elements.append(f'<instruction_error action="{action}" path="{path}">{e}</instruction_error>')
        else:
            payload_elements.append(f'<instruction_result action="{action}" path="{path}">{p}</instruction_result>')

    full_payload = "\n".join(payload_elements)
    full_errors = "\n".join(error_elements) if error_elements else "None"
    
    return format_tool_response("obsidian", payload=full_payload, errors=full_errors)

def _execute_single_action(action: str, target_path: str, path: str, content: str, search_term: str, vault_path: str) -> tuple[str, str]:
    try:
        if action == "read":
            if not os.path.exists(target_path):
                return "", f"Error: File not found at {path}"
            with open(target_path, "r") as f:
                return f.read(), "None"
        
        elif action == "write":
            if os.path.exists(target_path):
                return "", f"Error: File already exists at {path}. Use 'overwrite' if intentional."
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with open(target_path, "w") as f:
                f.write(content)
            return f"Successfully wrote to {path}", "None"
            
        elif action == "overwrite":
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with open(target_path, "w") as f:
                f.write(content)
            return f"Successfully overwrote {path}", "None"
            
        elif action == "append":
            if not os.path.exists(target_path):
                 return "", f"Error: File not found at {path} to append."
            with open(target_path, "a") as f:
                f.write(content)
            return f"Successfully appended to {path}", "None"
            
        elif action == "delete":
            if not os.path.exists(target_path):
                return "", f"Error: File not found at {path}"
            if not os.path.isfile(target_path):
                return "", f"Error: Path {path} is not a file. 'delete' action only deletes single files."
            os.remove(target_path)
            return f"Successfully deleted file {path}", "None"
            
        elif action == "file_search":
            if not os.path.exists(target_path):
                return "", f"Error: Path not found: {path}"
            search_dir = target_path if os.path.isdir(target_path) else os.path.dirname(target_path)
                
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
                return "", f"Error: Too many results ({total_results}). Please provide a search term to narrow down."
                
            if search_term:
                truncated_results = results[:N]
                output = "\n".join(truncated_results)
                output += f"\nshow {len(truncated_results)} out of {total_results} results"
                return output, "None"
            else:
                return "\n".join(results), "None"
                
        else:
            return "", f"Error: Unknown action '{action}'"
            
    except Exception as e:
        return "", f"Error: {e}"
