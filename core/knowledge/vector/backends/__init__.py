"""Backend selection for the vector store.

`KNOWLEDGE_BACKEND` decides which implementation `db.py` talks to:

    auto      (default) probe the hardware, prefer lancedb, fall back to numpy
    lancedb   force LanceDB
    numpy     force the numpy/tantivy store

Backend modules are imported lazily and only after selection. That ordering is
load-bearing, not stylistic: importing `lance_store` pulls in `lancedb`, which
kills the process outright on a CPU without AVX2.
"""

from typing import Optional

from core.knowledge.vector import probe

LANCEDB = "lancedb"
NUMPY = "numpy"
AUTO = "auto"

VALID_BACKENDS = (AUTO, LANCEDB, NUMPY)

_selected = None


def _load(backend_name: str):
    if backend_name == LANCEDB:
        from core.knowledge.vector.backends import lance_store
        return lance_store

    from core.knowledge.vector.backends import numpy_store
    return numpy_store


def resolve_backend_name(configured: Optional[str] = None) -> str:
    """Turns the configured value into a concrete backend name."""
    if configured is None:
        from core.util.config import Config
        configured = Config().knowledge_backend

    choice = (configured or AUTO).strip().lower()

    if choice == LANCEDB:
        return LANCEDB
    if choice == NUMPY:
        return NUMPY
    if choice != AUTO:
        print(f"Warning: unknown KNOWLEDGE_BACKEND '{configured}'; falling back to '{AUTO}'.")

    if probe.lancedb_usable():
        return LANCEDB

    print(
        "Notice: lancedb cannot run on this machine (likely a CPU without AVX2); "
        "using the numpy knowledge backend."
    )
    return NUMPY


def get_backend(configured: Optional[str] = None, force_reselect: bool = False):
    """Returns the active backend module, selecting it on first use."""
    global _selected
    if _selected is not None and not force_reselect and configured is None:
        return _selected

    backend = _load(resolve_backend_name(configured))
    if configured is None:
        _selected = backend
    return backend


def reset() -> None:
    """Clears the memoized backend. For tests."""
    global _selected
    _selected = None
