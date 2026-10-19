"""INVENTORY.md as a document: sections of markdown tables, edited row by row.

    ### 肉類 (Meats)
    | 食材名稱 | 剩餘份量 / 數量 | 入庫日期 | 預計保鮮期 / 備註 |
    | :--- | :--- | :--- | :--- |
    | 牛肉 (Beef) | 1 份 | 2026-09-20 | 冷藏/冷凍保存 |

A row's cells are name, quantity, purchase date and note. Every line that isn't
a changed row is written back exactly as read, so a rewrite never disturbs the
title, notes, headings, spacing or the rows nobody touched.

Besides rows, the file holds headings, table headers, notes ('> ...'), rules
('---') and blank lines. Any other line (a bullet typed by hand, a table missing
its separator line, a row with no name) is kept as written and listed by
`unrecognized()`, so a tool can return it raw instead of dropping it.
"""
import re
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple, Union

TITLE = "# 廚房庫存 (Kitchen Inventory)"
DEFAULT_HEADER = ("食材名稱", "剩餘份量 / 數量", "入庫日期", "預計保鮮期 / 備註")
NAME, QTY, ADDED, NOTE = range(4)

_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*$")
_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")
# Notes for the reader: '> ...', '<!-- ... -->', and rules ('---', '***', '___').
_NOTE = re.compile(r"^\s*(?:>|<!--|(?:-\s*){3,}$|(?:\*\s*){3,}$|(?:_\s*){3,}$)")
_PAREN_TAIL = re.compile(r"^(.*?)[(（]([^()（）]*)[)）]\s*$")


def normalize(text: str) -> str:
    """Case-blind, with full-width parentheses and runs of spaces folded."""
    text = (text or "").replace("（", "(").replace("）", ")")
    return " ".join(text.split()).casefold()


def name_aliases(name: str) -> Set[str]:
    """Every way a name can be referred to: '西洋芹 / 芹菜 (Celery)' is also
    '西洋芹 / 芹菜', '西洋芹', '芹菜' and 'Celery'."""
    aliases = {normalize(name)}
    m = _PAREN_TAIL.match((name or "").strip())
    if m:
        for part in m.groups():
            aliases.add(normalize(part))
            aliases.update(normalize(p) for p in re.split(r"[/／]", part))
    aliases.discard("")
    return aliases


def chinese_name(name: str) -> str:
    """The name without its parenthesized English: '羊肉 (Lamb)' -> '羊肉'."""
    m = _PAREN_TAIL.match((name or "").strip())
    return m.group(1).strip() if m and m.group(1).strip() else (name or "").strip()


def clean_cell(text: str) -> str:
    """Text safe to put in a table cell: one line, no column separators."""
    return " ".join(str(text or "").replace("|", "／").split())


@dataclass(eq=False)
class Row:
    section: str
    cells: List[str]
    original: Optional[str] = None  # the line as read; None for a new row
    dirty: bool = False

    @property
    def name(self) -> str:
        return self.cells[NAME]

    @property
    def qty(self) -> str:
        return self.cells[QTY]

    @property
    def added(self) -> str:
        return self.cells[ADDED]

    @property
    def note(self) -> str:
        return self.cells[NOTE]

    def set(self, column: int, value: str) -> None:
        value = clean_cell(value)
        if self.cells[column] != value:
            self.cells[column] = value
            self.dirty = True

    def render(self, newline: str) -> str:
        if self.original is not None and not self.dirty:
            return self.original
        return "| " + " | ".join(self.cells) + " |" + newline


class InventoryDoc:
    """The parsed file: a list of untouched lines and Row objects, in order."""

    def __init__(self, text: str):
        lines = text.splitlines(keepends=True)
        self.newline = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
        self.items: List[Union[str, Row]] = []
        self._unrecognized: List[str] = []
        section = ""
        i = 0
        while i < len(lines):
            line = lines[i]
            level, heading = _heading(line)
            if level >= 2:
                section = heading
            elif _is_table_line(line) and i + 1 < len(lines) and _is_separator(lines[i + 1]):
                self.items += [line, lines[i + 1]]
                i += 2
                while i < len(lines) and _is_table_line(lines[i]):
                    row = Row(section, _cells(lines[i]), original=lines[i])
                    if not row.name and any(row.cells):
                        self._unrecognized.append(lines[i].rstrip("\r\n"))
                    self.items.append(row)
                    i += 1
                continue
            elif line.strip() and not level and not _NOTE.match(line):
                self._unrecognized.append(line.rstrip("\r\n"))
            self.items.append(line)
            i += 1

    def unrecognized(self) -> List[str]:
        """The lines, as read, that are neither rows nor the file's structure."""
        return list(self._unrecognized)

    def rows(self) -> List[Row]:
        return [item for item in self.items if isinstance(item, Row) and item.name]

    def sections(self) -> List[str]:
        return [heading for level, heading in map(_heading, self._lines()) if level >= 2]

    def find_section(self, query: str) -> Optional[str]:
        """The heading `query` names: by its whole text, its Chinese or English part, or a
        piece of it ('Meats', '水果', 'Vegetables')."""
        aliases = name_aliases(query)
        if not aliases:
            return None
        headings = self.sections()
        for heading in headings:
            if aliases & name_aliases(heading):
                return heading
        return next((h for h in headings if any(a in normalize(h) for a in aliases)), None)

    def remove(self, row: Row) -> None:
        self.items = [item for item in self.items if item is not row]

    def insert(self, section_query: str, cells: List[str]) -> Tuple[Row, str, bool]:
        """Adds a row at the end of a section's table; (row, heading, section created?).

        A section `section_query` doesn't name is created at the end of the file.
        """
        heading = self.find_section(section_query)
        created = heading is None
        if created:
            heading = clean_cell(section_query)
            self._append_section(heading)
        row = Row(heading, [clean_cell(c) for c in cells], dirty=True)
        self.items.insert(self._table_end(heading), row)
        return row, heading, created

    def render(self) -> str:
        pieces = [item.render(self.newline) if isinstance(item, Row) else item for item in self.items]
        for i, piece in enumerate(pieces[:-1]):
            if not piece.endswith("\n"):
                pieces[i] = piece + self.newline
        return "".join(pieces)

    # ------------------------------------------------------------------ internals

    def _lines(self) -> List[str]:
        return [item for item in self.items if isinstance(item, str)]

    def _table_end(self, heading: str) -> int:
        """Where a new row of `heading` goes: after its table's last line (a table is
        started under the heading when it has none)."""
        start = next(i for i, item in enumerate(self.items)
                     if isinstance(item, str) and _heading(item)[0] >= 2 and _heading(item)[1] == heading)
        end = start + 1
        last_table_line = None
        while end < len(self.items):
            item = self.items[end]
            if isinstance(item, str) and _heading(item)[0] >= 1:
                break
            if isinstance(item, Row) or _is_table_line(item):
                last_table_line = end
            end += 1
        if last_table_line is not None:
            return last_table_line + 1
        self._ensure_newline(start)
        header, separator = self._table_header()
        self.items[start + 1:start + 1] = [header, separator]
        return start + 3

    def _append_section(self, heading: str) -> None:
        if not self.items:
            self.items.append(TITLE + self.newline)
        self._ensure_newline(len(self.items) - 1)
        last = self.items[-1]
        if isinstance(last, Row) or last.strip():
            self.items.append(self.newline)  # a blank line before the new heading
        header, separator = self._table_header()
        self.items += [f"### {heading}{self.newline}", header, separator]

    def _table_header(self) -> Tuple[str, str]:
        """The header and separator lines of the first table, so new tables match."""
        for i, item in enumerate(self.items[:-1]):
            nxt = self.items[i + 1]
            if isinstance(item, str) and isinstance(nxt, str) and _is_table_line(item) and _is_separator(nxt):
                return _with_newline(item, self.newline), _with_newline(nxt, self.newline)
        header = "| " + " | ".join(DEFAULT_HEADER) + " |" + self.newline
        return header, "| " + " | ".join([":---"] * len(DEFAULT_HEADER)) + " |" + self.newline

    def _ensure_newline(self, index: int) -> None:
        item = self.items[index]
        if isinstance(item, str) and not item.endswith("\n"):
            self.items[index] = item + self.newline


def _heading(line: str) -> Tuple[int, str]:
    """(level, text) of a markdown heading line; (0, '') for any other line."""
    m = _HEADING.match(line.rstrip("\r\n"))
    return (len(m.group(1)), m.group(2)) if m else (0, "")


def _is_table_line(line: str) -> bool:
    return line.strip().startswith("|")


def _is_separator(line: str) -> bool:
    cells = _cells(line, pad=False)
    return _is_table_line(line) and bool(cells) and all(_SEPARATOR_CELL.match(c) for c in cells)


def _cells(line: str, pad: bool = True) -> List[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    cells = [c.strip() for c in text.split("|")]
    while pad and len(cells) < len(DEFAULT_HEADER):
        cells.append("")
    return cells


def _with_newline(line: str, newline: str) -> str:
    return line if line.endswith("\n") else line + newline
