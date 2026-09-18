"""Public interface to the vault knowledge store.

This module is a facade. It holds no storage logic of its own -- every call is
forwarded to whichever backend `backends.get_backend()` selected -- and it
imports nothing native at module scope.

That last point is the whole reason the facade exists. `import lancedb` on a CPU
without AVX2 executes an illegal instruction, and SIGILL is not an exception:
the kernel kills the interpreter before any `except` clause runs. A module-scope
`import lancedb` here therefore took the entire bot down the moment anything
touched the knowledge stack, including the tool loader merely discovering
`vault_search`. Keeping this module pure-Python means importing it is always
safe, and the decision about which native library to load happens later, behind
a subprocess probe.

The handle returned by `init_knowledge_db` is a `StoreHandle`, not a LanceDB
table. It remembers which backend created it, so these functions dispatch on the
handle rather than on global state -- which is what lets a single process hold
handles from both backends at once.
"""

from typing import Any, Dict, List, Optional

from core.knowledge.vector import backends
from core.knowledge.vector.backends.base import TABLE_NAME, StoreHandle

__all__ = [
    "TABLE_NAME",
    "StoreHandle",
    "get_knowledge_db_path",
    "get_active_backend_name",
    "get_db_connection",
    "get_vault_schema",
    "init_knowledge_db",
    "build_fts_index",
    "get_existing_hashes",
    "upsert_chunks",
    "prune_deleted_files",
    "add_chunks",
    "count_chunks",
    "scan_by_category",
    "flush",
    "hybrid_search_vault",
]


def _backend(handle: Any = None):
    """Resolves the backend to use for a call.

    Prefers the one that produced the handle, so a handle stays bound to its
    own implementation even if the process-wide selection differs.
    """
    if isinstance(handle, StoreHandle):
        return handle.backend
    return backends.get_backend()


def get_knowledge_db_path() -> str:
    """Returns the configured store directory, defaulting to ~/pkm/.lancedb."""
    from core.util.config import Config
    return Config().knowledge_db_path


def get_active_backend_name() -> str:
    """Name of the backend currently in use ('lancedb' or 'numpy')."""
    return backends.get_backend().name


def get_db_connection(db_path: Optional[str] = None) -> Any:
    """Opens a connection to the store directory.

    The return type is backend-defined and only meaningful when handed straight
    back to `init_knowledge_db`.
    """
    return _backend().connect(db_path or get_knowledge_db_path())


def get_vault_schema(dim: int = 1536):
    """PyArrow schema for vault chunks.

    Kept at module level because the parquet the numpy backend writes uses the
    same field order, so the schema is the shared on-disk contract rather than a
    LanceDB detail. pyarrow is imported lazily only to keep this module's import
    free of native code.
    """
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


def init_knowledge_db(
    conn: Optional[Any] = None,
    db_path: Optional[str] = None,
    table_name: str = TABLE_NAME,
    dim: Optional[int] = None,
    force_recreate: bool = False,
) -> StoreHandle:
    """Opens (or creates) the vault chunk store and returns a handle to it."""
    if dim is None:
        from core.util.config import Config
        dim = Config().embedding_dimensions

    backend = _backend()
    return backend.open(
        conn=conn,
        db_path=db_path or get_knowledge_db_path(),
        table_name=table_name,
        dim=dim,
        force_recreate=force_recreate,
    )


def build_fts_index(table: StoreHandle) -> None:
    """Creates or refreshes the BM25 index over the enriched `text` column."""
    return _backend(table).build_fts_index(table)


def get_existing_hashes(table: StoreHandle) -> Dict[str, str]:
    """Maps chunk id -> content_hash for everything currently stored."""
    return _backend(table).get_existing_hashes(table)


def upsert_chunks(table: StoreHandle, chunks: List[Dict[str, Any]]) -> Dict[str, int]:
    """Inserts new chunks and updates changed ones, keyed on content_hash.

    Returns {'inserted', 'updated', 'unchanged', 'total_scanned'}.
    """
    return _backend(table).upsert_chunks(table, chunks)


def prune_deleted_files(table: StoreHandle, current_file_paths: List[str]) -> int:
    """Removes chunks whose file is gone. Returns the number of *files* pruned."""
    return _backend(table).prune_deleted_files(table, current_file_paths)


def add_chunks(table: StoreHandle, rows: List[Dict[str, Any]]) -> None:
    """Appends rows without dedupe checks -- the bulk rebuild path."""
    return _backend(table).add_chunks(table, rows)


def count_chunks(table: StoreHandle) -> int:
    """Number of chunks stored."""
    return _backend(table).count_chunks(table)


def scan_by_category(table: StoreHandle, category: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns full rows, vectors included, optionally filtered by category.

    Separate from `hybrid_search_vault` because search results deliberately omit
    vectors; wiki_scanner needs them to compare documents against each other.
    """
    return _backend(table).scan_by_category(table, category)


def flush(table: StoreHandle) -> None:
    """Forces pending writes to disk.

    A no-op for LanceDB, which persists on write. The numpy backend batches its
    writes, so anything that must survive the process needs this.
    """
    return _backend(table).flush(table)


def hybrid_search_vault(
    table: StoreHandle,
    query: str,
    query_vector: Optional[List[float]] = None,
    limit: int = 5,
    category: Optional[str] = None,
    path_filter: Optional[str] = None,
    search_type: str = "hybrid",
) -> List[Dict[str, Any]]:
    """Searches vault chunks by vector, BM25, or a fusion of both.

    Args:
        table: Handle from `init_knowledge_db`.
        query: Keyword or natural language query.
        query_vector: Dense query embedding; required for hybrid and semantic.
        limit: Max results.
        category: 'vault', 'wiki', or None/'all' for no filter.
        path_filter: Substring match against file_path.
        search_type: 'hybrid', 'semantic'/'vector', or 'keyword'/'fts'.
            Falls back to keyword when no query_vector is supplied.
    """
    return _backend(table).hybrid_search(
        table,
        query=query,
        query_vector=query_vector,
        limit=limit,
        category=category,
        path_filter=path_filter,
        search_type=search_type,
    )
