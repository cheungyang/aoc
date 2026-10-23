"""Helpers for building SQLite statements and looking inside a database.

Identifiers are only ever names from code (a table, a column, a sanitized
session key), so they are checked against a plain-identifier pattern and
quoted rather than escaped; values always go in as `?` parameters.
"""
import json
import re
import sqlite3
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

_IDENTIFIER = re.compile(r"^[^\W\d]\w*$")  # word characters, Unicode included, not starting with a digit


def is_identifier(name: str) -> bool:
    """True for a name made of letters, digits and underscores (any script), not starting with a digit."""
    return bool(_IDENTIFIER.match(name or ""))


def ident(name: str) -> str:
    """`name` as a quoted SQL identifier. Anything but a plain identifier raises ValueError."""
    if not is_identifier(name):
        raise ValueError(f"not a plain SQL identifier: {name!r}")
    return f'"{name}"'


def placeholders(count: int) -> str:
    """`?, ?, ?` for `count` values."""
    return ", ".join("?" * count)


def bind(value: Any) -> Any:
    """A value as SQLite stores it: an Enum by its value, anything else as is."""
    return value.value if isinstance(value, Enum) else value


def decode_json(row: Dict[str, Any], columns: Iterable[str]) -> Dict[str, Any]:
    """`row` with the JSON text in `columns` decoded, in place. Text that isn't JSON stays as it is."""
    for column in columns:
        value = row.get(column)
        if value and isinstance(value, str):
            try:
                row[column] = json.loads(value)
            except ValueError:
                pass
    return row


def where_clause(where: Optional[Mapping[str, Any]]) -> Tuple[str, List[Any]]:
    """` WHERE …` and its parameters for `{column: value}`, all conditions ANDed.

    `None` matches NULL, a list/tuple/set matches any of its values (an empty
    one matches nothing), anything else matches by equality.
    """
    if not where:
        return "", []
    parts, params = [], []
    for column, value in where.items():
        name = ident(column)
        if value is None:
            parts.append(f"{name} IS NULL")
        elif isinstance(value, (list, tuple, set, frozenset)):
            values = [bind(v) for v in value]
            parts.append(f"{name} IN ({placeholders(len(values))})" if values else "0")
            params.extend(values)
        else:
            parts.append(f"{name} = ?")
            params.append(bind(value))
    return " WHERE " + " AND ".join(parts), params


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def table_names(conn: sqlite3.Connection, prefix: str = "") -> List[str]:
    """Tables whose name starts with `prefix`, in creation order.

    The prefix is matched literally: in LIKE, `_` would match any character.
    """
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return [row[0] for row in rows if row[0].startswith(prefix)]
