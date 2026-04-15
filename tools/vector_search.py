import os
from langchain_core.tools import tool
from core.memory.vault_vector_store import VaultVectorStore
from core.util import format_tool_response

@tool
def vector_search(action: str, vault_id: str, agent_id: str, search_term: str = "") -> str:
    """
    Perform vector operations on Obsidian vaults.
    Actions: vector_search, update_vectors.
    """
    if not agent_id:
        return format_tool_response("vector_search", payload="", errors="Error: agent_id is required to verify permissions.")

    from core.loaders.tools_loader import ToolsLoader
    tools_loader = ToolsLoader()
    
    # Resolve vault path (assuming it's in the workspace root)
    workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    vault_path = os.path.abspath(os.path.join(workspace_root, vault_id))
    
    if not os.path.exists(vault_path):
        return format_tool_response("vector_search", payload="", errors=f"Error: Vault path not found: {vault_path}")

    # Check permission using the new tool name 'vector_search'
    if not tools_loader.check_permission(agent_id, "vector_search", action, vault_id=vault_id):
        return format_tool_response("vector_search", payload="", errors=f"Error: Agent {agent_id} does not have permission to perform '{action}' on vault {vault_id}")

    persist_dir = os.path.join(vault_path, ".chroma_db")
    store = VaultVectorStore(vault_dir=vault_path, persist_dir=persist_dir)

    try:
        if action == "vector_search":
            if not search_term:
                return format_tool_response("vector_search", payload="", errors="Error: search_term is required for vector_search.")
            results = store.search(query=search_term, limit=5)
            
            output = []
            for res in results:
                output.append(f"File: {res['path']}\nContent: {res['content']}\nDistance: {res['distance']}\n---")
            return format_tool_response("vector_search", payload="\n".join(output) if output else "No relevant content found.", errors="None")
            
        elif action == "update_vectors":
            store.index_vault()
            return format_tool_response("vector_search", payload="Vectors updated successfully.", errors="None")
            
        else:
            return format_tool_response("vector_search", payload="", errors=f"Error: Unknown action '{action}'")
            
    except Exception as e:
        return format_tool_response("vector_search", payload="", errors=f"Error performing vector action: {e}")
