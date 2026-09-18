"""Vector store built from numpy, parquet and tantivy.

Exists because LanceDB's x86_64 wheels assume an AVX2-era CPU and the deployment
NAS (Celeron J4025, Goldmont Plus) has only SSE4.2 -- importing lancedb there
raises SIGILL. Every dependency used here dispatches on CPU features at runtime,
so this backend runs anywhere Python does.

It is an exhaustive scan rather than an ANN index, which is the right trade at
this scale: the vault is ~3.4MB of markdown, so even 20k chunks is a 118MB
float32 matrix and a single matmul per query. An approximate index would add
failure modes to save milliseconds.

Layout under the store directory:

    chunks.parquet   scalar columns, same field order as the LanceDB schema
    vectors.npy      float32 [n_chunks, dim], row-aligned with the parquet
    fts/             tantivy BM25 index over the enriched `text` column
"""

import atexit
import json
import os
import shutil
from typing import Any, Dict, List, Optional

from core.knowledge.vector.backends.base import (
    SCALAR_FIELDS,
    StoreHandle,
    classify_upserts,
    empty_upsert_stats,
    normalize_result,
    reciprocal_rank_fusion,
)

name = "numpy"

CHUNKS_FILE = "chunks.parquet"
VECTORS_FILE = "vectors.npy"
FTS_DIR = "fts"

# Handles that have unflushed mutations. `add_chunks` is called in a loop during
# a rebuild, and flushing a 118MB matrix per batch would dominate the runtime,
# so writes are deferred -- this makes an interpreter exit still durable.
_DIRTY: List["_State"] = []


class _State:
    """In-memory mirror of one store directory."""

    def __init__(self, path: str, dim: int):
        self.path = path
        self.dim = dim
        self.rows: List[Dict[str, Any]] = []
        self.vectors = None            # np.ndarray (n, dim) float32
        self.index_by_id: Dict[str, int] = {}
        self.dirty = False
        self._searcher = None          # cached tantivy index

    # -- paths ----------------------------------------------------------
    @property
    def chunks_path(self) -> str:
        return os.path.join(self.path, CHUNKS_FILE)

    @property
    def vectors_path(self) -> str:
        return os.path.join(self.path, VECTORS_FILE)

    @property
    def fts_path(self) -> str:
        return os.path.join(self.path, FTS_DIR)

    def reindex(self):
        self.index_by_id = {r["id"]: i for i, r in enumerate(self.rows)}

    def mark_dirty(self):
        if not self.dirty:
            self.dirty = True
            _DIRTY.append(self)


def _np():
    import numpy as np
    return np


def _self():
    import sys
    return sys.modules[__name__]


def _empty_vectors(dim: int):
    np = _np()
    return np.zeros((0, dim), dtype=np.float32)


# ----------------------------------------------------------------------
# Open / persistence
# ----------------------------------------------------------------------

def connect(db_path: Optional[str]):
    """No server to connect to; the directory is the connection."""
    path = os.path.abspath(os.path.expanduser(db_path))
    os.makedirs(path, exist_ok=True)
    return path


def open(conn: Any, db_path: Optional[str], table_name: str,
         dim: int, force_recreate: bool) -> StoreHandle:
    path = conn if isinstance(conn, str) else connect(db_path)
    # Tables are separate directories so several can coexist in one store path.
    table_dir = os.path.join(path, table_name)

    if force_recreate and os.path.isdir(table_dir):
        shutil.rmtree(table_dir, ignore_errors=True)
    os.makedirs(table_dir, exist_ok=True)

    state = _State(table_dir, dim)
    if not force_recreate:
        _load(state)

    if state.vectors is None:
        state.vectors = _empty_vectors(dim)

    return StoreHandle(backend=_self(), native=state, name=table_name, path=table_dir)


def _load(state: _State) -> None:
    np = _np()
    if not os.path.exists(state.chunks_path):
        state.rows = []
        state.vectors = _empty_vectors(state.dim)
        state.reindex()
        return

    import pyarrow.parquet as pq

    table = pq.read_table(state.chunks_path)
    data = table.to_pydict()
    n = len(data.get("id", []))
    state.rows = [
        {f: (data.get(f) or [None] * n)[i] for f in SCALAR_FIELDS}
        for i in range(n)
    ]

    if os.path.exists(state.vectors_path):
        vectors = np.load(state.vectors_path)
        # A dimension change means the embedding model changed; the stored
        # vectors are meaningless against new queries, so drop them rather than
        # silently comparing incompatible spaces.
        if vectors.shape[0] != n or (vectors.size and vectors.shape[1] != state.dim):
            vectors = _empty_vectors(state.dim)
            state.rows = []
    else:
        vectors = _empty_vectors(state.dim)
        state.rows = []

    state.vectors = vectors.astype(np.float32, copy=False)
    state.reindex()


def flush(h: StoreHandle) -> None:
    state = h.native if isinstance(h, StoreHandle) else h
    if not state.dirty:
        return

    np = _np()
    import pyarrow as pa
    import pyarrow.parquet as pq

    os.makedirs(state.path, exist_ok=True)
    columns = {f: [r.get(f) for r in state.rows] for f in SCALAR_FIELDS}
    pq.write_table(pa.table(columns), state.chunks_path)
    np.save(state.vectors_path, state.vectors)

    state.dirty = False
    if state in _DIRTY:
        _DIRTY.remove(state)


def _flush_all_at_exit():
    for state in list(_DIRTY):
        try:
            flush(state)
        except Exception:
            pass


atexit.register(_flush_all_at_exit)


# ----------------------------------------------------------------------
# Reads
# ----------------------------------------------------------------------

def count_chunks(h: StoreHandle) -> int:
    return len(h.native.rows)


def get_existing_hashes(h: StoreHandle) -> Dict[str, str]:
    return {r["id"]: r.get("content_hash", "") for r in h.native.rows}


def scan_by_category(h: StoreHandle, category: Optional[str]) -> List[Dict[str, Any]]:
    state = h.native
    rows = []
    for i, row in enumerate(state.rows):
        if category and category.lower() != "all":
            if (row.get("category") or "").lower() != category.lower():
                continue
        out = dict(row)
        out["vector"] = state.vectors[i].tolist() if len(state.vectors) > i else []
        rows.append(out)
    return rows


# ----------------------------------------------------------------------
# Writes
# ----------------------------------------------------------------------

def add_chunks(h: StoreHandle, rows: List[Dict[str, Any]]) -> None:
    """Appends without checking for existing ids -- the bulk rebuild path."""
    if not rows:
        return

    np = _np()
    state = h.native

    new_vectors = np.array(
        [r.get("vector") or [0.0] * state.dim for r in rows], dtype=np.float32
    )
    if new_vectors.ndim == 1:
        new_vectors = new_vectors.reshape(len(rows), -1)

    state.rows.extend({f: r.get(f) for f in SCALAR_FIELDS} for r in rows)
    state.vectors = (
        new_vectors if state.vectors is None or len(state.vectors) == 0
        else np.vstack([state.vectors, new_vectors])
    )
    state.reindex()
    state.mark_dirty()


def upsert_chunks(h: StoreHandle, chunks: List[Dict[str, Any]]) -> Dict[str, int]:
    if not chunks:
        return empty_upsert_stats()

    np = _np()
    state = h.native
    to_write, stats = classify_upserts(chunks, get_existing_hashes(h))
    if not to_write:
        return stats

    appended_rows = []
    appended_vectors = []

    for chunk in to_write:
        row = {f: chunk.get(f) for f in SCALAR_FIELDS}
        vector = np.asarray(chunk.get("vector") or [0.0] * state.dim, dtype=np.float32)

        existing = state.index_by_id.get(chunk["id"])
        if existing is None:
            appended_rows.append(row)
            appended_vectors.append(vector)
        else:
            state.rows[existing] = row
            state.vectors[existing] = vector

    if appended_rows:
        state.rows.extend(appended_rows)
        stacked = np.array(appended_vectors, dtype=np.float32)
        state.vectors = (
            stacked if len(state.vectors) == 0 else np.vstack([state.vectors, stacked])
        )

    state.reindex()
    state.mark_dirty()
    build_fts_index(h)
    return stats


def prune_deleted_files(h: StoreHandle, current_file_paths: List[str]) -> int:
    """Drops rows for files no longer on disk. Returns the count of *files*."""
    np = _np()
    state = h.native
    if not state.rows:
        return 0

    current_set = set(current_file_paths)
    present = {r.get("file_path") for r in state.rows}
    deleted_files = present - current_set
    if not deleted_files:
        return 0

    keep = [i for i, r in enumerate(state.rows) if r.get("file_path") in current_set]
    state.rows = [state.rows[i] for i in keep]
    state.vectors = (
        state.vectors[keep] if len(keep) else _empty_vectors(state.dim)
    )
    state.reindex()
    state.mark_dirty()
    build_fts_index(h)
    return len(deleted_files)


# ----------------------------------------------------------------------
# Full-text (BM25) via tantivy
# ----------------------------------------------------------------------

def _tantivy_schema():
    import tantivy

    builder = tantivy.SchemaBuilder()
    builder.add_text_field("id", stored=True)
    builder.add_text_field("text", stored=False)
    return builder.build()


def build_fts_index(h: StoreHandle) -> None:
    """Rebuilds the BM25 index over the enriched `text` column.

    Rebuilt wholesale rather than updated in place: at this corpus size it takes
    well under a second, and it removes any chance of the index disagreeing with
    the rows after an update or a prune.
    """
    state = h.native
    try:
        import tantivy

        flush(h)  # keep parquet and the FTS index describing the same rows
        if os.path.isdir(state.fts_path):
            shutil.rmtree(state.fts_path, ignore_errors=True)
        os.makedirs(state.fts_path, exist_ok=True)

        index = tantivy.Index(_tantivy_schema(), path=state.fts_path)
        writer = index.writer()
        for row in state.rows:
            writer.add_document(tantivy.Document(
                id=row.get("id") or "",
                text=row.get("text") or "",
            ))
        writer.commit()
        index.reload()
        state._searcher = index
    except Exception as e:
        print(f"Warning: Failed to create FTS index: {e}")
        state._searcher = None


def _get_index(state: _State):
    if state._searcher is not None:
        return state._searcher
    if not os.path.isdir(state.fts_path):
        return None
    try:
        import tantivy
        index = tantivy.Index(_tantivy_schema(), path=state.fts_path)
        index.reload()
        state._searcher = index
        return index
    except Exception:
        return None


def _bm25_ranking(state: _State, query: str, limit: int) -> List[tuple]:
    """Returns (id, bm25_score) pairs ordered by relevance, best first."""
    index = _get_index(state)
    if index is None or not query.strip():
        return []

    try:
        parsed = index.parse_query(query, ["text"])
    except Exception:
        # Query syntax the parser rejects (stray operators, quotes). Fall back to
        # the bare terms so a user's punctuation cannot turn into an error.
        safe = " ".join(w for w in "".join(
            c if c.isalnum() or c.isspace() else " " for c in query
        ).split())
        if not safe:
            return []
        try:
            parsed = index.parse_query(safe, ["text"])
        except Exception:
            return []

    try:
        searcher = index.searcher()
        hits = searcher.search(parsed, limit).hits
        return [(searcher.doc(addr)["id"][0], float(score)) for score, addr in hits]
    except Exception:
        return []


# ----------------------------------------------------------------------
# Search
# ----------------------------------------------------------------------

def _filter_indices(state: _State, category: Optional[str], path_filter: Optional[str]) -> List[int]:
    indices = []
    for i, row in enumerate(state.rows):
        if category and category.lower() != "all":
            if (row.get("category") or "").lower() != category.lower():
                continue
        if path_filter and path_filter not in (row.get("file_path") or ""):
            continue
        indices.append(i)
    return indices


def _vector_ranking(state: _State, query_vector, candidates: List[int], limit: int) -> List[tuple]:
    """Returns (id, cosine_similarity) pairs, best first."""
    np = _np()
    if not candidates or query_vector is None or len(state.vectors) == 0:
        return []

    q = np.asarray(query_vector, dtype=np.float32)
    if q.shape[0] != state.vectors.shape[1]:
        return []

    matrix = state.vectors[candidates]
    q_norm = np.linalg.norm(q)
    m_norms = np.linalg.norm(matrix, axis=1)
    denom = m_norms * (q_norm or 1.0)
    denom[denom == 0] = 1.0

    sims = (matrix @ q) / denom
    top = np.argsort(-sims)[:limit]
    return [(state.rows[candidates[int(i)]]["id"], float(sims[int(i)])) for i in top]


def hybrid_search(h: StoreHandle, query: str, query_vector: Optional[List[float]],
                  limit: int, category: Optional[str], path_filter: Optional[str],
                  search_type: str) -> List[Dict[str, Any]]:
    state = h.native
    if not state.rows:
        return []

    search_type = (search_type or "hybrid").lower()
    candidates = _filter_indices(state, category, path_filter)
    if not candidates:
        return []

    allowed = {state.rows[i]["id"] for i in candidates}
    # Over-fetch before filtering: BM25 runs against the whole index, so asking
    # for exactly `limit` would lose matches once a category filter is applied.
    fetch = max(limit * 5, limit)

    use_vector = query_vector is not None
    if search_type in ("semantic", "vector") and use_vector:
        # Report the cosine similarity itself rather than a rank-derived number,
        # so the score means the same thing it does on the LanceDB backend.
        scored = _vector_ranking(state, query_vector, candidates, limit)
    elif search_type in ("keyword", "fts") or not use_vector:
        scored = [p for p in _bm25_ranking(state, query, fetch) if p[0] in allowed][:limit]
    else:
        vector_ranked = _vector_ranking(state, query_vector, candidates, fetch)
        bm25_ranked = [p for p in _bm25_ranking(state, query, fetch) if p[0] in allowed]
        # Fused on rank, not on raw score: BM25 and cosine live on unrelated
        # scales, so the RRF value is the only meaningful number here.
        fused = reciprocal_rank_fusion([
            [doc_id for doc_id, _ in vector_ranked],
            [doc_id for doc_id, _ in bm25_ranked],
        ])
        scored = sorted(fused.items(), key=lambda kv: -kv[1])[:limit]

    results = []
    for doc_id, score in scored:
        idx = state.index_by_id.get(doc_id)
        if idx is None:
            continue
        results.append(normalize_result(state.rows[idx], float(score)))
    return results
