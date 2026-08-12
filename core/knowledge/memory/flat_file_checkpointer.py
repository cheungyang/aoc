"""
Backwards-compatible wrapper around SqliteCheckpointer.
"""
from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer, sanitize_table_name

class FlatFileCheckpointer(SqliteCheckpointer):
    """Backwards compatibility wrapper for SqliteCheckpointer."""
    def __init__(self, directory: str = None, db_path: str = None):
        if db_path is not None:
            super().__init__(db_path=db_path)
        elif directory is not None:
            import os
            super().__init__(db_path=os.path.join(directory, "memory.db"))
        else:
            super().__init__()
