import os
import sys
from dotenv import load_dotenv

# Resolve workspace root dynamically
workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Load environment variables from workspace root
load_dotenv(os.path.join(workspace_root, ".env"))

# Add workspace root to path
sys.path.append(workspace_root)

from core.memory.vault_vector_store import VaultVectorStore

vault_dir = os.path.join(workspace_root, "pkm")
persist_dir = os.path.join(workspace_root, "pkm", ".chroma_db")

def main():
    print("Starting reindexing of the vault...")
    try:
        store = VaultVectorStore(vault_dir=vault_dir, persist_dir=persist_dir)
        store.index_vault()
        print("Reindexing completed successfully.")
    except Exception as e:
        print(f"Error during reindexing: {e}")

if __name__ == "__main__":
    main()
