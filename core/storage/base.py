"""The SQLite store base class: one file, the schema it owns, and CRUD of its entries.

A store says where its file lives and which schema files it owns
(core/storage/schemas/<name>.sql); its methods hold the SQL for its tables,
built on the helpers here:

    class ScheduleState(Store):
        schema = ("schedule_state",)

        def default_path(self) -> str:
            return Config().memory_db_path

        def get(self, schedule_id: str) -> dict:
            row = self.select_one("schedule_state", {"schedule_id": schedule_id})
            return dict(row) if row else {}

Every call opens its own connection and closes it, as the stores always have:
tools and the logging handler run in worker threads, and one shared
connection would need locking. `connect()` creates the directory, the file and
the schema (every schema file is idempotent) and commits when its body
succeeds; `read_only()` never creates anything. The CRUD helpers take `conn=`
to run inside a caller's `connect()` block, as one transaction, and a store's
own methods join it the same way, with `connect(conn)`.

In WAL mode, though, the last connection to close folds the WAL into the file
and syncs it, so where every connection closes, nearly every write pays that.
`hold_open()` keeps one idle connection to a file for the process's life, which
no call uses; main.py holds memory.db's.
"""
import os
import sqlite3
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple, Union

from core.storage.util import bind, ident, is_identifier, placeholders, where_clause

SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"

Row = sqlite3.Row

# The idle connections hold_open() keeps, one per file, for the process's life.
_HELD: Dict[str, sqlite3.Connection] = {}


class StoreMissing(FileNotFoundError):
    """A store opened read-only whose file doesn't exist."""


@lru_cache(maxsize=None)
def load_schema(name: str) -> str:
    """The DDL in core/storage/schemas/<name>.sql."""
    return (SCHEMAS_DIR / f"{name}.sql").read_text(encoding="utf-8")


def apply_schema(conn: sqlite3.Connection, name: str, **identifiers: str) -> None:
    """Creates whatever of schema `name` doesn't exist yet.

    A schema whose table name varies writes `{{key}}` where the name goes;
    `identifiers[key]` fills it, and must be a plain identifier.
    """
    sql = load_schema(name)
    for key, value in identifiers.items():
        if not is_identifier(value):
            raise ValueError(f"{name}.sql: {key}={value!r} is not a plain SQL identifier")
        sql = sql.replace("{{" + key + "}}", value)
    if "{{" in sql:
        raise KeyError(f"{name}.sql has a placeholder left unfilled")
    conn.executescript(sql)


class Store:
    """One SQLite file and the tables a subclass owns in it.

    Subclasses set:
    - `schema`: schema files applied by `init()`, in order;
    - `optional_schema`: applied after them, skipped if they fail (FTS5 may be missing);
    - `pragmas`: run on every read-write connection, e.g. "journal_mode = WAL";
    - `init_on_connect`: False for a store whose schema is created only by an
      explicit `init()`, so a connection can open a file someone else built;
    - `default_path()`: where the file is when none is given, resolved per call.
    """

    schema: ClassVar[Tuple[str, ...]] = ()
    optional_schema: ClassVar[Tuple[str, ...]] = ()
    pragmas: ClassVar[Tuple[str, ...]] = ()
    init_on_connect: ClassVar[bool] = True

    def __init__(self, path: Optional[str] = None):
        self._path = path

    def default_path(self) -> str:
        raise NotImplementedError(f"{type(self).__name__} has no default path; pass one")

    @property
    def path(self) -> str:
        return os.path.expanduser(self._path or self.default_path())

    def exists(self) -> bool:
        return os.path.isfile(self.path)

    # -- connections ---------------------------------------------------------

    @contextmanager
    def connect(self, conn: Optional[sqlite3.Connection] = None) -> Iterator[sqlite3.Connection]:
        """A read-write connection: creates the directory, the file and the schema; commits if the body succeeds.

        Given `conn`, joins it instead: yields it as is, and the block that
        opened it commits and closes it.
        """
        if conn is not None:
            yield conn
            return
        with self._open(init=self.init_on_connect) as own:
            yield own

    @contextmanager
    def read_only(self) -> Iterator[sqlite3.Connection]:
        """A connection that can't write and never creates anything; StoreMissing if the file isn't there."""
        file = Path(self.path)
        if not file.is_file():
            raise StoreMissing(str(file))
        conn = sqlite3.connect(f"{file.resolve().as_uri()}?mode=ro", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            yield conn
        finally:
            conn.close()

    def hold_open(self) -> bool:
        """Keeps an idle connection to the file open for the rest of the process; False if one already is.

        In WAL mode, the last connection to close folds the WAL into the file,
        syncs both and deletes the WAL; the next write then syncs a fresh WAL's
        header. Every call above closes its connection, so with none held,
        nearly every close is the last, and on a slow disk those syncs are most
        of a write's time (65 ms each on the NAS). With one held, no other close
        is the last, in this process or another, and SQLite checkpoints on its
        own each time the WAL reaches ~1,000 pages.

        The connection runs the store's pragmas and one read, each to its end,
        and nothing after, so it never keeps a read open: one would stop every
        checkpoint at its snapshot and let the WAL grow without bound.
        """
        path = os.path.abspath(self.path)
        if path in _HELD:
            return False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False)  # never used again, so any thread may close it
        for statement in (*(f"PRAGMA {pragma}" for pragma in self.pragmas), "SELECT count(*) FROM sqlite_master"):
            conn.execute(statement).fetchall()
        mode = conn.execute("PRAGMA journal_mode").fetchall()[0][0]
        _HELD[path] = conn
        print(f"[storage] Holding {path} open (journal_mode={mode}), so closing other connections won't checkpoint it")
        return True

    def release_hold(self) -> None:
        """Closes the connection hold_open() keeps to the file, if any. As the last one, it folds the WAL in."""
        conn = _HELD.pop(os.path.abspath(self.path), None)
        if conn is not None:
            conn.close()

    def init(self, conn: Optional[sqlite3.Connection] = None, **identifiers: str) -> None:
        """Creates whatever of the schema doesn't exist yet.

        `identifiers` fill the schema's `{{key}}` placeholders, for a store whose
        tables are named per use (see `apply_schema`).
        """
        if conn is None:
            with self._open(init=False) as own:
                self._create(own, **identifiers)
        else:
            self._create(conn, **identifiers)

    @contextmanager
    def _open(self, init: bool) -> Iterator[sqlite3.Connection]:
        path = self.path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        conn = sqlite3.connect(path)  # its default 5 s timeout is the busy timeout
        try:
            conn.row_factory = sqlite3.Row
            for pragma in self.pragmas:
                conn.execute(f"PRAGMA {pragma}")
            if init:
                self._create(conn)
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _create(self, conn: sqlite3.Connection, **identifiers: str) -> None:
        for name in self.schema:
            apply_schema(conn, name, **identifiers)
        for name in self.optional_schema:
            try:
                apply_schema(conn, name, **identifiers)
            except sqlite3.Error as e:
                print(f"[storage] {type(self).__name__}: skipped {name}.sql: {e}")

    # -- CRUD of entries -----------------------------------------------------

    def insert(self, table: str, row: Mapping[str, Any], *, or_: Optional[str] = None,
               conn: Optional[sqlite3.Connection] = None) -> Optional[int]:
        """Adds one entry and returns its rowid, or None if `or_="IGNORE"` skipped it.

        `or_` is "IGNORE" or "REPLACE", for INSERT OR IGNORE / OR REPLACE.
        """
        if or_ not in (None, "IGNORE", "REPLACE"):
            raise ValueError(f"or_ must be IGNORE or REPLACE, not {or_!r}")
        columns = list(row)
        verb = f"INSERT OR {or_}" if or_ else "INSERT"
        sql = (f"{verb} INTO {ident(table)} ({', '.join(map(ident, columns))}) "
               f"VALUES ({placeholders(len(columns))})")
        with self.connect(conn) as c:
            cursor = c.execute(sql, [bind(row[column]) for column in columns])
            return cursor.lastrowid if cursor.rowcount else None

    def upsert(self, table: str, row: Mapping[str, Any], key: Union[str, Sequence[str]], *,
               conn: Optional[sqlite3.Connection] = None) -> None:
        """Adds one entry, or updates the columns given if one with the same `key` exists."""
        keys = (key,) if isinstance(key, str) else tuple(key)
        columns = list(row)
        updates = [column for column in columns if column not in keys]
        action = ("DO UPDATE SET " + ", ".join(f"{ident(c)} = excluded.{ident(c)}" for c in updates)
                  if updates else "DO NOTHING")
        sql = (f"INSERT INTO {ident(table)} ({', '.join(map(ident, columns))}) "
               f"VALUES ({placeholders(len(columns))}) "
               f"ON CONFLICT ({', '.join(map(ident, keys))}) {action}")
        with self.connect(conn) as c:
            c.execute(sql, [bind(row[column]) for column in columns])

    def select(self, table: str, where: Optional[Mapping[str, Any]] = None, *,
               columns: Union[str, Sequence[str]] = "*", order_by: Optional[str] = None,
               limit: Optional[int] = None, conn: Optional[sqlite3.Connection] = None) -> List[Row]:
        """The entries matching `where` (see util.where_clause). `order_by` is SQL from code, e.g. "at DESC"."""
        projection = "*" if columns == "*" else ", ".join(map(ident, columns))
        clause, params = where_clause(where)
        sql = f"SELECT {projection} FROM {ident(table)}{clause}"
        if order_by:
            sql += f" ORDER BY {order_by}"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self.connect(conn) as c:
            return c.execute(sql, params).fetchall()

    def select_one(self, table: str, where: Optional[Mapping[str, Any]] = None, *,
                   columns: Union[str, Sequence[str]] = "*", order_by: Optional[str] = None,
                   conn: Optional[sqlite3.Connection] = None) -> Optional[Row]:
        rows = self.select(table, where, columns=columns, order_by=order_by, limit=1, conn=conn)
        return rows[0] if rows else None

    def update(self, table: str, values: Mapping[str, Any], where: Mapping[str, Any], *,
               conn: Optional[sqlite3.Connection] = None) -> int:
        """Sets `values` on the entries matching `where`; returns how many changed."""
        if not where:
            raise ValueError("update() needs a condition; use execute() to change every row")
        clause, params = where_clause(where)
        sql = f"UPDATE {ident(table)} SET {', '.join(f'{ident(c)} = ?' for c in values)}{clause}"
        with self.connect(conn) as c:
            return c.execute(sql, [bind(v) for v in values.values()] + params).rowcount

    def delete(self, table: str, where: Mapping[str, Any], *,
               conn: Optional[sqlite3.Connection] = None) -> int:
        """Removes the entries matching `where`; returns how many."""
        if not where:
            raise ValueError("delete() needs a condition; use execute() to remove every row")
        clause, params = where_clause(where)
        with self.connect(conn) as c:
            return c.execute(f"DELETE FROM {ident(table)}{clause}", params).rowcount

    def count(self, table: str, where: Optional[Mapping[str, Any]] = None, *,
              conn: Optional[sqlite3.Connection] = None) -> int:
        clause, params = where_clause(where)
        with self.connect(conn) as c:
            return c.execute(f"SELECT count(*) FROM {ident(table)}{clause}", params).fetchone()[0]

    def query(self, sql: str, params: Sequence[Any] = (), *,
              conn: Optional[sqlite3.Connection] = None) -> List[Row]:
        """Rows of a statement the helpers above don't cover."""
        with self.connect(conn) as c:
            return c.execute(sql, [bind(p) for p in params]).fetchall()

    def execute(self, sql: str, params: Sequence[Any] = (), *,
                conn: Optional[sqlite3.Connection] = None) -> int:
        """Runs a statement the helpers above don't cover; returns its rowcount."""
        with self.connect(conn) as c:
            return c.execute(sql, [bind(p) for p in params]).rowcount
