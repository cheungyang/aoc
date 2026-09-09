"""
Memory package providing session storage and checkpointing via SQLite.
"""
from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer, sanitize_table_name
from core.knowledge.memory.sqlite_session_store import SqliteSessionStore

# Backwards-compatible aliases
FlatFileCheckpointer = SqliteCheckpointer
FlatFileSessionStore = SqliteSessionStore

__all__ = [
    "SqliteCheckpointer",
    "SqliteSessionStore",
    "FlatFileCheckpointer",
    "FlatFileSessionStore",
    "sanitize_table_name",
]
