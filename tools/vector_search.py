import os
from langchain_core.tools import tool
from core.memory.vault_vector_store import VaultVectorStore

@tool
def vector_search(action: str, vault_id: str, agent_id: str, search_term: str = "") -> str:
    """
    Perform vector operations on Obsidian vaults.
    Actions: vector_search, update_vectors.
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

    # Check permission using the new tool name 'vector_search'
    if not tools_loader.check_permission(agent_id, "vector_search", action, vault_id=vault_id):
        return f"Error: Agent {agent_id} does not have permission to perform '{action}' on vault {vault_id}"

    persist_dir = os.path.join(vault_path, ".chroma_db")
    store = VaultVectorStore(vault_dir=vault_path, persist_dir=persist_dir)

    try:
        if action == "vector_search":
            if not search_term:
                return "Error: search_term is required for vector_search."
            results = store.search(query=search_term, limit=5)
            
            output = []
            for res in results:
                output.append(f"File: {res['path']}\nContent: {res['content']}\nDistance: {res['distance']}\n---")
            return "\n".join(output) if output else "No relevant content found."
            
        elif action == "update_vectors":
            store.index_vault()
            return "Vectors updated successfully."
            
        else:
            return f"Error: Unknown action '{action}'"
            
    except Exception as e:
        return f"Error performing vector action: {e}"
