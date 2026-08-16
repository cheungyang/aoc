#!/usr/bin/env python3
"""
wiki_scanner.py

A State & Semantic Radar script to be run as a crontab.
It identifies a list of topics for wiki-gardener agent to propose updates to the pkm/wiki/ space.
Outputs to: pkm/wiki/pending_lint.json
"""
import os
import json
import numpy as np
import lancedb
from datetime import datetime
from dateutil.relativedelta import relativedelta

def compute_cosine_similarity(vectors):
    """Computes pairwise cosine similarity for a batch of vectors."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1
    normalized = vectors / norms
    return np.dot(normalized, normalized.T)

def run_scanner():
    # Determine paths
    # Assuming script is in dev/langgraph/scripts/wiki_scanner.py
    workspace_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    
    db_path = os.path.join(workspace_dir, "pkm", ".lancedb")
    if not os.path.exists(db_path):
        # Fallback to current working directory execution
        db_path = "pkm/.lancedb"

    print(f"Connecting to LanceDB at: {db_path}")
    db = lancedb.connect(db_path)
    
    try:
        table = db.open_table("vault_chunks")
    except Exception as e:
        print(f"Failed to open vault_chunks table: {e}")
        return

    print("Fetching wiki chunks...")
    arrow_tbl = table.search().where("category = 'wiki'").to_arrow()
    data = arrow_tbl.to_pydict()
    
    ids = data.get("id", [])
    file_paths = data.get("file_path", [])
    tags = data.get("tags", [])
    vectors = data.get("vector", [])
    updated_at = data.get("updated_at", [])
    
    n_chunks = len(ids)
    print(f"Loaded {n_chunks} wiki chunks.")
    
    now = datetime.now()
    six_months_ago = now - relativedelta(months=6)
    
    stale_files = set()
    dup_files = set()
    
    print("Processing Stale Stubs...")
    # 1. Stale stubs
    for i in range(n_chunks):
        fp = file_paths[i]
        tag_str = tags[i]
        
        is_stub = False
        if tag_str:
            try:
                tag_list = json.loads(tag_str)
                if any("stub" in str(t).lower() or "#stub" in str(t).lower() for t in tag_list):
                    is_stub = True
            except json.JSONDecodeError:
                if "stub" in str(tag_str).lower():
                    is_stub = True
                    
        if is_stub:
            up_str = updated_at[i]
            if up_str:
                try:
                    up_str_clean = up_str.replace("Z", "+00:00")
                    up_time = datetime.fromisoformat(up_str_clean)
                    if up_time.tzinfo is not None:
                        up_time = up_time.replace(tzinfo=None)
                    
                    if up_time < six_months_ago:
                        stale_files.add(fp)
                except Exception:
                    pass

    print("Processing Semantic Duplicates...")
    # 2. Semantic duplicates (Document-level)
    if n_chunks > 0:
        import re
        
        # Group chunks by file
        file_vectors = {}
        file_texts = {}
        for i in range(n_chunks):
            fp = file_paths[i]
            if fp not in file_vectors:
                file_vectors[fp] = []
                file_texts[fp] = ""
            file_vectors[fp].append(vectors[i])
            file_texts[fp] += " " + str(tags[i]) + " " + str(data.get("text", [])[i]).lower()

        doc_paths = []
        doc_vectors = []
        doc_words = []
        
        for fp, vecs in file_vectors.items():
            # Average the chunk vectors for document-level embedding
            avg_vec = np.mean(vecs, axis=0)
            norm = np.linalg.norm(avg_vec)
            if norm > 0:
                avg_vec = avg_vec / norm
            doc_vectors.append(avg_vec)
            doc_paths.append(fp)
            # Extract words for Jaccard similarity
            words = set(re.findall(r'\w+', file_texts[fp]))
            doc_words.append(words)
            
        doc_vectors_np = np.array(doc_vectors, dtype=np.float32)
        sim_matrix = compute_cosine_similarity(doc_vectors_np)
        
        # We only care about upper triangular to avoid A->A and duplicate pairs
        xs, ys = np.where(np.triu(sim_matrix, k=1) > 0.92)
        
        for x, y in zip(xs, ys):
            fp1 = doc_paths[x]
            fp2 = doc_paths[y]
            
            w1 = doc_words[x]
            w2 = doc_words[y]
            
            # Avoid division by zero
            if not w1 or not w2:
                continue
                
            # Jaccard similarity filter to eliminate vastly different topics
            jaccard = len(w1 & w2) / len(w1 | w2)
            
            if jaccard > 0.15:
                # Store as sorted tuple to ensure distinct file pairs are unique
                pair = tuple(sorted([fp1, fp2]))
                dup_files.add(pair)
                
    output = {
        "stale_stubs": sorted(list(stale_files)),
        "duplicate_candidates": [{"file1": p[0], "file2": p[1]} for p in sorted(list(dup_files))]
    }
    
    out_path = os.path.join(workspace_dir, "pkm", "wiki", "pending_lint.json")
    if not os.path.exists(os.path.dirname(out_path)):
        # Fallback
        out_path = "pkm/wiki/pending_lint.json"
        
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
        
    print(f"Scanner finished. Results saved to {out_path}")
    print(f"Found {len(stale_files)} stale stubs and {len(dup_files)} duplicate file pairs.")

if __name__ == "__main__":
    run_scanner()
