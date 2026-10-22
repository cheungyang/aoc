"""SQLite schemas: stored as `.sql` files beside this module, applied by name.

    conn = sqlite3.connect(path)
    apply_schema(conn, "channel_inbox")

Every schema file is idempotent (`CREATE TABLE IF NOT EXISTS ...`), so a
store applies its schema on each connection, as the stores did inline before.
"""
import sqlite3
from functools import lru_cache
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def load_schema(name: str) -> str:
    """The DDL in `core/db/<name>.sql`."""
    return (SCHEMA_DIR / f"{name}.sql").read_text(encoding="utf-8")


def apply_schema(conn: sqlite3.Connection, name: str) -> None:
    """Creates whatever of schema `name` doesn't exist yet."""
    conn.executescript(load_schema(name))
