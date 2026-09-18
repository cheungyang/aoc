"""LanceDB backend -- the original implementation, behind the backend protocol.

This module is the only place allowed to import `lancedb`, and it is imported
lazily by `backends/__init__.py` after the runtime has confirmed the CPU can
actually run it. On hardware without AVX2 the import raises SIGILL, which kills
the interpreter outright rather than raising -- so it must never be reachable
from a module-scope import anywhere else.
"""

from typing import Any, Dict, List, Optional

from core.knowledge.vector.backends.base import (
    SCALAR_FIELDS,
    TABLE_NAME,
    StoreHandle,
    classify_upserts,
    empty_upsert_stats,
    normalize_result,
)

name = "lancedb"


def _lancedb():
    import lancedb
    return lancedb


def get_vault_schema(dim: int = 1536):
    """PyArrow schema for vault chunks."""
    import pyarrow as pa

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
        pa.field("updated_at", pa.string()),
    ])


def connect(db_path: Optional[str]):
    import os
    path = os.path.abspath(os.path.expanduser(db_path))
    os.makedirs(path, exist_ok=True)
    return _lancedb().connect(path)


def open(conn: Any, db_path: Optional[str], table_name: str,
         dim: int, force_recreate: bool) -> StoreHandle:
    if conn is None:
        conn = connect(db_path)

    schema = get_vault_schema(dim)

    if force_recreate:
        table = conn.create_table(table_name, schema=schema, mode="overwrite")
        return StoreHandle(backend=_self(), native=table, name=table_name, path=db_path or "")

    try:
        table = conn.open_table(table_name)
        if not set(schema.names).issubset(set(table.schema.names)):
            # Schema evolution: recreate with the new schema.
            table = conn.create_table(table_name, schema=schema, mode="overwrite")
    except Exception:
        table = conn.create_table(table_name, schema=schema)

    return StoreHandle(backend=_self(), native=table, name=table_name, path=db_path or "")


def _self():
    import sys
    return sys.modules[__name__]


def count_chunks(h: StoreHandle) -> int:
    return h.native.count_rows()


def add_chunks(h: StoreHandle, rows: List[Dict[str, Any]]) -> None:
    if rows:
        h.native.add(rows)


def build_fts_index(h: StoreHandle) -> None:
    """Creates or refreshes the BM25 index on the enriched `text` column."""
    table = h.native
    try:
        from lancedb.index import FTS
        table.create_index("text", config=FTS(), replace=True)
    except Exception:
        try:
            table.create_fts_index("text", replace=True)
        except Exception as e:
            print(f"Warning: Failed to create FTS index: {e}")


def get_existing_hashes(h: StoreHandle) -> Dict[str, str]:
    table = h.native
    if table.count_rows() == 0:
        return {}

    try:
        arrow_tbl = table.to_arrow()
        ids = arrow_tbl.column("id").to_pylist()
        hashes = arrow_tbl.column("content_hash").to_pylist()
        return dict(zip(ids, hashes))
    except Exception:
        results = table.search().select(["id", "content_hash"]).limit(100000).to_list()
        return {r["id"]: r.get("content_hash", "") for r in results}


def upsert_chunks(h: StoreHandle, chunks: List[Dict[str, Any]]) -> Dict[str, int]:
    if not chunks:
        return empty_upsert_stats()

    to_write, stats = classify_upserts(chunks, get_existing_hashes(h))

    if to_write:
        h.native.merge_insert("id") \
            .when_matched_update_all() \
            .when_not_matched_insert_all() \
            .execute(to_write)
        build_fts_index(h)

    return stats


def prune_deleted_files(h: StoreHandle, current_file_paths: List[str]) -> int:
    table = h.native
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
            safe_f = f.replace("'", "''")
            table.delete(f"file_path = '{safe_f}'")
            pruned_count += 1

        if pruned_count > 0:
            build_fts_index(h)
        return pruned_count
    except Exception as e:
        print(f"Warning: Failed to prune deleted files: {e}")
        return 0


def scan_by_category(h: StoreHandle, category: Optional[str]) -> List[Dict[str, Any]]:
    """Returns full rows -- vector included -- optionally filtered by category.

    `hybrid_search` deliberately drops the vector from its results; wiki_scanner
    needs it to compute document-level similarity, so it gets its own entry point
    rather than widening the search contract.
    """
    table = h.native
    if table.count_rows() == 0:
        return []

    builder = table.search()
    if category and category.lower() != "all":
        safe_cat = category.lower().replace("'", "''")
        builder = builder.where(f"category = '{safe_cat}'")

    arrow_tbl = builder.to_arrow()
    data = arrow_tbl.to_pydict()

    rows = []
    n = len(data.get("id", []))
    for i in range(n):
        row = {f: data.get(f, [None] * n)[i] for f in SCALAR_FIELDS}
        vectors = data.get("vector")
        row["vector"] = list(vectors[i]) if vectors is not None else []
        rows.append(row)
    return rows


def hybrid_search(h: StoreHandle, query: str, query_vector: Optional[List[float]],
                  limit: int, category: Optional[str], path_filter: Optional[str],
                  search_type: str) -> List[Dict[str, Any]]:
    table = h.native
    if table.count_rows() == 0:
        return []

    search_type = (search_type or "hybrid").lower()

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

    return [
        normalize_result(r, r.get("_relevance_score", r.get("_distance", 0.0)))
        for r in raw_results
    ]


def flush(h: StoreHandle) -> None:
    """No-op: LanceDB persists on write."""
    return None
