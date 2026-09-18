"""Shared contract and helpers for vector store backends.

`db.py` is a thin facade over one of these backends. Everything a backend must
implement is listed in `VectorStore`; everything two backends would otherwise
duplicate lives here.

The handle passed around by callers is deliberately opaque: it carries the
backend that produced it, so a process can hold handles from different backends
at once. That is what lets the contract test suite run the same assertions
against every implementation.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

TABLE_NAME = "vault_chunks"

# Column order is shared by both backends so the parquet a numpy store writes
# stays readable by anything that understands the LanceDB schema.
SCALAR_FIELDS = [
    "id",
    "file_path",
    "category",
    "title",
    "header_path",
    "tags",
    "text",
    "raw_content",
    "content_hash",
    "updated_at",
]


@dataclass
class StoreHandle:
    """Opaque store reference returned by `init_knowledge_db`.

    Callers never inspect this. The facade uses `backend` to dispatch and hands
    `native` back to that backend, which is the only code that knows its shape.
    """

    backend: Any
    native: Any
    name: str = TABLE_NAME
    path: str = ""


@runtime_checkable
class VectorStore(Protocol):
    """Operations a backend must provide for the facade to be complete."""

    name: str

    def connect(self, db_path: Optional[str]) -> Any: ...
    def open(self, conn: Any, db_path: Optional[str], table_name: str,
             dim: int, force_recreate: bool) -> StoreHandle: ...
    def count_chunks(self, h: StoreHandle) -> int: ...
    def add_chunks(self, h: StoreHandle, rows: List[Dict[str, Any]]) -> None: ...
    def upsert_chunks(self, h: StoreHandle, chunks: List[Dict[str, Any]]) -> Dict[str, int]: ...
    def get_existing_hashes(self, h: StoreHandle) -> Dict[str, str]: ...
    def prune_deleted_files(self, h: StoreHandle, current_file_paths: List[str]) -> int: ...
    def build_fts_index(self, h: StoreHandle) -> None: ...
    def scan_by_category(self, h: StoreHandle, category: Optional[str]) -> List[Dict[str, Any]]: ...
    def hybrid_search(self, h: StoreHandle, query: str, query_vector: Optional[List[float]],
                      limit: int, category: Optional[str], path_filter: Optional[str],
                      search_type: str) -> List[Dict[str, Any]]: ...
    def flush(self, h: StoreHandle) -> None: ...


def empty_upsert_stats(total_scanned: int = 0) -> Dict[str, int]:
    return {"inserted": 0, "updated": 0, "unchanged": 0, "total_scanned": total_scanned}


def classify_upserts(chunks: List[Dict[str, Any]], existing_hashes: Dict[str, str]):
    """Splits incoming chunks into what actually needs writing.

    Shared because the insert/update/unchanged accounting is part of the public
    contract -- `sync_knowledge` prints these numbers -- and two backends
    computing it separately would be two chances to disagree.

    Returns `(to_write, stats)`.
    """
    to_write = []
    stats = empty_upsert_stats(total_scanned=len(chunks))
    seen = set()

    for chunk in chunks:
        cid = chunk["id"]
        if cid in seen:
            continue
        seen.add(cid)

        chash = chunk.get("content_hash", "")
        if cid not in existing_hashes:
            to_write.append(chunk)
            stats["inserted"] += 1
        elif existing_hashes[cid] != chash:
            to_write.append(chunk)
            stats["updated"] += 1
        else:
            stats["unchanged"] += 1

    return to_write, stats


def normalize_result(row: Dict[str, Any], score: float) -> Dict[str, Any]:
    """Shapes a stored row into the dict shape `vault_search` and its tests expect."""
    import json

    tags = []
    raw_tags = row.get("tags")
    if raw_tags:
        try:
            tags = json.loads(raw_tags)
        except Exception:
            tags = [raw_tags]

    return {
        "id": row.get("id"),
        "file_path": row.get("file_path"),
        "category": row.get("category", "vault"),
        "title": row.get("title", ""),
        "header_path": row.get("header_path", ""),
        "tags": tags,
        "text": row.get("text", ""),
        "raw_content": row.get("raw_content", ""),
        "score": score,
        "updated_at": row.get("updated_at", ""),
    }


def reciprocal_rank_fusion(rankings: List[List[str]], k: int = 60) -> Dict[str, float]:
    """Combines several ranked id lists into one score per id.

    RRF is what LanceDB uses for its hybrid mode, so reproducing it keeps result
    ordering comparable between backends. It also sidesteps the real problem
    with blending BM25 and cosine directly: the two scores live on unrelated
    scales, whereas ranks do not.
    """
    scores: Dict[str, float] = {}
    for ranked in rankings:
        for position, doc_id in enumerate(ranked):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + position + 1)
    return scores
