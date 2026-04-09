import os
import uuid
import chromadb
from langchain_google_genai import GoogleGenerativeAIEmbeddings
import re

class VaultVectorStore:
    def __init__(self, vault_dir: str, persist_dir: str):
        self.vault_dir = vault_dir
        self.persist_dir = persist_dir
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.collection = self.client.get_or_create_collection(name="pkm_vault")
        gemini_key = os.getenv("GEMINI_API_KEY")
        self.embeddings = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-001", api_key=gemini_key)

    def index_vault(self):
        print(f"Clearing collection 'pkm_vault' for clean re-index")
        try:
            self.client.delete_collection(name="pkm_vault")
        except Exception as e:
            print(f"Info: Collection pkm_vault might not exist yet: {e}")
        self.collection = self.client.get_or_create_collection(name="pkm_vault")

        documents = []
        metadatas = []
        ids = []
        
        print(f"Scanning vault directory: {self.vault_dir}")
        for root, dirs, files in os.walk(self.vault_dir):
            for file in files:
                if file.endswith('.md'):
                    path = os.path.join(root, file)
                    rel_path = os.path.relpath(path, self.vault_dir)
                    
                    # Skip hidden files and directories
                    if any(part.startswith('.') for part in rel_path.split(os.sep)):
                        continue
                        
                    try:
                        with open(path, 'r', encoding='utf-8') as f:
                            content = f.read()
                        if content.strip():
                            # 1. Remove dataviewjs blocks
                            content = re.sub(r'```dataviewjs[\s\S]*?```', '', content)
                            
                            # 2. Extract Frontmatter
                            frontmatter = ""
                            fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
                            if fm_match:
                                frontmatter = fm_match.group(1)
                                
                            # 3. Extract Tags
                            tags = re.findall(r'#([\w/-]+)', content)
                            tags_str = ",".join(sorted(list(set(tags)))) if tags else ""
                            
                            # 4. Extract Priorities
                            priority_map = {"🔺": "Highest", "⏫": "High", "🔼": "Medium", "🔽": "Low", "⏬": "Lowest"}
                            priorities = [label for char, label in priority_map.items() if char in content]
                            highest_priority = priorities[0] if priorities else ""
                            
                            # 5. Extract Tasks
                            tasks = re.findall(r'^-\s*\[\s*\]\s*(.*)', content, re.MULTILINE)
                            
                            # Create summary chunk
                            summary_parts = []
                            if frontmatter:
                                summary_parts.append(f"Frontmatter:\n{frontmatter}")
                            if tags:
                                summary_parts.append(f"Tags: {', '.join(sorted(list(set(tags))))}")
                            if priorities:
                                summary_parts.append(f"Priorities: {', '.join(set(priorities))}")
                            if tasks:
                                summary_parts.append(f"Tasks:\n" + "\n".join([f"- {t}" for t in tasks[:20]])) # Limit to 20 tasks in summary
                                
                            if summary_parts:
                                summary_content = f"File Summary for {rel_path}:\n" + "\n\n".join(summary_parts)
                                documents.append(summary_content)
                                metadatas.append({'path': rel_path, 'chunk': '__summary__', 'tags': tags_str, 'priority': highest_priority})
                                ids.append(f"{rel_path}___summary__")
                            
                            # Split by paragraphs for chunking
                            paragraphs = content.split('\n\n')
                            for i, para in enumerate(paragraphs):
                                stripped_para = para.strip()
                                if len(stripped_para) > 10: # Skip tiny chunks like single punctuation
                                    documents.append(stripped_para)
                                    metadatas.append({'path': rel_path, 'chunk': i, 'tags': tags_str, 'priority': highest_priority})
                                    ids.append(f"{rel_path}_{i}")
                    except Exception as e:
                        print(f"Error reading {path}: {e}")

        if not documents:
            print("No documents found to index.")
            return

        print(f"Generating embeddings and adding to Chroma for {len(documents)} chunks...")
        
        batch_size = 50
        import time
        
        for i in range(0, len(documents), batch_size):
            batch_docs = documents[i:i+batch_size]
            batch_metas = metadatas[i:i+batch_size]
            batch_ids = ids[i:i+batch_size]
            
            print(f"Processing batch {i//batch_size + 1} of {(len(documents) - 1)//batch_size + 1}...")
            
            try:
                batch_embeds = self.embeddings.embed_documents(batch_docs)
            except Exception as e:
                print(f"Error generating embeddings for batch: {e}")
                if "RESOURCE_EXHAUSTED" in str(e):
                    print("Rate limit hit. Sleeping 30 seconds before retry...")
                    time.sleep(30)
                    try:
                        batch_embeds = self.embeddings.embed_documents(batch_docs)
                    except Exception as e2:
                        print(f"Retry failed: {e2}")
                        return
                else:
                    return
                
            try:
                self.collection.upsert(
                    embeddings=batch_embeds,
                    documents=batch_docs,
                    metadatas=batch_metas,
                    ids=batch_ids
                )
            except Exception as e:
                print(f"Error adding batch to Chroma: {e}")
                return
                
            if i + batch_size < len(documents):
                print("Sleeping 2 seconds to avoid rate limits...")
                time.sleep(2)
                
        print(f"Successfully indexed {len(documents)} chunks into Chroma.")

    def search(self, query: str, limit: int = 5):
        query_embed = self.embeddings.embed_query(query)
        
        results = self.collection.query(
            query_embeddings=[query_embed],
            n_results=limit
        )
        
        formatted_results = []
        if results['documents']:
            for doc, meta, dist in zip(results['documents'][0], results['metadatas'][0], results['distances'][0]):
                formatted_results.append({
                    'path': meta['path'],
                    'content': doc,
                    'distance': dist
                })
                
        return formatted_results
