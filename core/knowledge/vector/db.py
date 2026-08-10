import os
import json
import hashlib
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, Set

import pyarrow as pa
import lancedb
from lancedb.index import FTS

from core.config import Config

TABLE_NAME = "vault_chunks"


def get_knowledge_db_path() -> str:
    """Returns the configured LanceDB directory path or defaults to ~/pkm/.lancedb."""
    return Config().knowledge_db_path


def get_db_connection(db_path: Optional[str] = None) -> lancedb.DBConnection:
    """Creates a LanceDB connection."""
    path = os.path.abspath(os.path.expanduser(db_path or get_knowledge_db_path()))
    os.makedirs(path, exist_ok=True)
    return lancedb.connect(path)


def get_vault_schema(dim: int = 1536) -> pa.Schema:
    """Returns the PyArrow schema for vault chunks."""
    return pa.schema([
        pa.field("id", pa.string()),
        pa.field("file_path", pa.string()),
        pa.field("category", pa.string()),
        pa.field("title", pa.string()),
        pa.field("header_path", pa.string()),
        pa.field("tags", pa.string()),
        pa.field("text", pa.string()),
        pa.field("raw_content", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), dim)),
        pa.field("content_hash", pa.string()),
        pa.field("updated_at", pa.string())
    ])


def init_knowledge_db(
    conn: Optional[lancedb.DBConnection] = None,
    db_path: Optional[str] = None,
    table_name: str = TABLE_NAME,
    dim: Optional[int] = None,
    force_recreate: bool = False
) -> Any:
    """
    Initializes or opens the LanceDB table for vault knowledge and creates the FTS index.
    """
    if conn is None:
        conn = get_db_connection(db_path)

    dimension = dim or Config().embedding_dimensions
    schema = get_vault_schema(dimension)

    if force_recreate:
        return conn.create_table(table_name, schema=schema, mode="overwrite")

    try:
        table = conn.open_table(table_name)
        existing_field_names = set(table.schema.names)
        expected_field_names = set(schema.names)
        if not expected_field_names.issubset(existing_field_names):
            # Schema evolution: Recreate table with new schema
            table = conn.create_table(table_name, schema=schema, mode="overwrite")
    except Exception:
        table = conn.create_table(table_name, schema=schema)

    return table


def build_fts_index(table: Any):
    """Creates or updates the Full-Text Search (Tantivy BM25) index on the text column."""
    try:
        table.create_index("text", config=FTS(), replace=True)
    except Exception:
        try:
            table.create_fts_index("text", replace=True)
        except Exception as e:
            print(f"Warning: Failed to create FTS index: {e}")


def get_existing_hashes(table: Any) -> Dict[str, str]:
    """
    Retrieves a mapping of id -> content_hash for all existing chunks in the table.
    """
    if table.count_rows() == 0:
        return {}
    
    try:
        arrow_tbl = table.to_arrow()
        ids = arrow_tbl.column("id").to_pylist()
        hashes = arrow_tbl.column("content_hash").to_pylist()
        return dict(zip(ids, hashes))
    except Exception:
        # Fallback via search/scan if to_arrow fails
        results = table.search().select(["id", "content_hash"]).limit(100000).to_list()
        return {r["id"]: r.get("content_hash", "") for r in results}


def upsert_chunks(table: Any, chunks: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Inserts new chunks and updates modified chunks based on content_hash.
    Returns stats dict: {'inserted': int, 'updated': int, 'unchanged': int, 'total_scanned': int}.
    """
    if not chunks:
        return {"inserted": 0, "updated": 0, "unchanged": 0, "total_scanned": 0}

    existing_hashes = get_existing_hashes(table)
    to_upsert = []
    inserted_count = 0
    updated_count = 0
    unchanged_count = 0

    seen_ids = set()
    for chunk in chunks:
        cid = chunk["id"]
        if cid in seen_ids:
            continue
        seen_ids.add(cid)

        chash = chunk.get("content_hash", "")
        if cid not in existing_hashes:
            to_upsert.append(chunk)
            inserted_count += 1
        elif existing_hashes[cid] != chash:
            to_upsert.append(chunk)
            updated_count += 1
        else:
            unchanged_count += 1

    if to_upsert:
        table.merge_insert("id") \
            .when_matched_update_all() \
            .when_not_matched_insert_all() \
            .execute(to_upsert)
        build_fts_index(table)

    return {
        "inserted": inserted_count,
        "updated": updated_count,
        "unchanged": unchanged_count,
        "total_scanned": len(chunks)
    }


def prune_deleted_files(table: Any, current_file_paths: List[str]) -> int:
    """
    Removes chunks from LanceDB whose file_path is no longer in current_file_paths.
    """
    if table.count_rows() == 0:
        return 0

    current_set = set(current_file_paths)
    try:
        arrow_tbl = table.to_arrow()
        file_paths = set(arrow_tbl.column("file_path").to_pylist())
        deleted_files = file_paths - current_set
        if not deleted_files:
            return 0
        
        pruned_count = 0
        for f in deleted_files:
            # Escape single quotes in file paths
            safe_f = f.replace("'", "''")
            table.delete(f"file_path = '{safe_f}'")
            pruned_count += 1
            
        if pruned_count > 0:
            build_fts_index(table)
        return pruned_count
    except Exception as e:
        print(f"Warning: Failed to prune deleted files: {e}")
        return 0


def hybrid_search_vault(
    table: Any,
    query: str,
    query_vector: Optional[List[float]] = None,
    limit: int = 5,
    category: Optional[str] = None,
    path_filter: Optional[str] = None,
    search_type: str = "hybrid"
) -> List[Dict[str, Any]]:
    """
    Performs hybrid, vector-only, or full-text (BM25) search on vault chunks.
    
    Args:
        table: LanceDB table instance.
        query: Keyword or natural language query.
        query_vector: Dense vector representation of query (required for hybrid & semantic).
        limit: Max results to return.
        category: Filter by category ('vault' for personal notes, 'wiki' for synthesized wiki, or 'all'/None).
        path_filter: Substring or prefix to filter file_path.
        search_type: 'hybrid', 'semantic' (vector only), or 'keyword' (BM25 only).
    """
    if table.count_rows() == 0:
        return []

    search_type = search_type.lower()

    if search_type == "hybrid" and query_vector is not None:
        builder = table.search(query_type="hybrid").vector(query_vector).text(query)
    elif search_type in ("semantic", "vector") and query_vector is not None:
        builder = table.search(query_vector)
    elif search_type in ("keyword", "fts") or query_vector is None:
        builder = table.search(query, query_type="fts")
    else:
        builder = table.search(query_type="hybrid").vector(query_vector).text(query)

    where_clauses = []
    if category and category.lower() != "all":
        safe_cat = category.lower().replace("'", "''")
        where_clauses.append(f"category = '{safe_cat}'")

    if path_filter:
        safe_path = path_filter.replace("'", "''")
        where_clauses.append(f"file_path LIKE '%{safe_path}%'")

    if where_clauses:
        builder = builder.where(" AND ".join(where_clauses))

    raw_results = builder.limit(limit).to_list()

    results = []
    for r in raw_results:
        tags = []
        if r.get("tags"):
            try:
                tags = json.loads(r["tags"])
            except Exception:
                tags = [r["tags"]]

        score = r.get("_relevance_score", r.get("_distance", 0.0))
        results.append({
            "id": r.get("id"),
            "file_path": r.get("file_path"),
            "category": r.get("category", "vault"),
            "title": r.get("title", ""),
            "header_path": r.get("header_path", ""),
            "tags": tags,
            "text": r.get("text", ""),
            "raw_content": r.get("raw_content", ""),
            "score": score,
            "updated_at": r.get("updated_at", "")
        })

    return results
