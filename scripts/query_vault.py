import os
import sys
from dotenv import load_dotenv

# Load environment variables from workspace root
load_dotenv("/Users/alvac/dev/langgraph/.env")

# Add workspace root to path
sys.path.append("/Users/alvac/dev/langgraph")

from core.memory.vault_vector_store import VaultVectorStore

vault_dir = "/Users/alvac/dev/langgraph/pkm"
persist_dir = "/Users/alvac/dev/langgraph/pkm/.chroma_db"

def main():
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
    else:
        query = input("Enter search query: ")
        
    if not query.strip():
        print("Empty query.")
        return
        
    print(f"Searching for: '{query}'...")
    try:
        store = VaultVectorStore(vault_dir=vault_dir, persist_dir=persist_dir)
        results = store.search(query, limit=10)
        
        print(f"\nFound {len(results)} results:\n")
        for i, res in enumerate(results):
            print(f"[{i+1}] File: {res['path']}")
            print(f"    Distance: {res['distance']:.4f}")
            print(f"    Snippet: {res['content'].replace('\n', ' ')[:200]}...")
            print("-" * 40)
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
