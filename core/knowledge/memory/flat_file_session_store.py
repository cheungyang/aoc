"""
Backwards-compatible wrapper around SqliteSessionStore.
"""
from core.knowledge.memory.sqlite_session_store import SqliteSessionStore, sanitize_table_name

class FlatFileSessionStore(SqliteSessionStore):
    """Backwards compatibility wrapper for SqliteSessionStore."""
    def __init__(self, sessions_dir: str = None, db_path: str = None):
        if db_path is not None:
            super().__init__(db_path=db_path)
        elif sessions_dir is not None:
            import os
            super().__init__(db_path=os.path.join(sessions_dir, "memory.db"))
        else:
            super().__init__()
