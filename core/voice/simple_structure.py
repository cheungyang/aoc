"""Deterministic fast path: speaks simply structured text without a model call.

Most structured replies are shallow: a heading, a flat list of short items,
some bold. Reading those aloud needs no judgement -- drop the markers, end
each item as a sentence, say the number of a numbered step -- so they are
rendered here by rule rather than costing a model round-trip before the
first word. Anything this renderer is not sure it can say faithfully
(nesting, code, wide tables, checklists, long items) returns None and the
`Verbalizer` sends it to the model. When in doubt, it declines.
"""
import re
from typing import List, Optional

# Numbered lists are read with ordinals taken from the author's own numbers,
# so a long list that `VoiceStream` splits across blocks keeps counting
# correctly. Bullets carry no order, and get none: each item is its own
# sentence.
_ORDINALS = (
    "First", "Second", "Third", "Fourth", "Fifth",
    "Sixth", "Seventh", "Eighth", "Ninth", "Tenth",
)

FAST_PATH_MAX_ITEMS = 10
FAST_PATH_MAX_ITEM_CHARS = 160
FAST_PATH_MAX_TABLE_ROWS = 8

# Markers whose meaning a rule cannot carry into speech: code, struck-out text
# (deleted, or done?), images, and checkbox tasks (the checked state matters).
_COMPLEX_RE = re.compile(r"```|~~|!\[|^\s*(?:[-*+]|\d+[.)])\s+\[[ xX]\]", re.MULTILINE)
_LIST_LINE_RE = re.compile(r"^([ \t]*)([-*+]|\d+[.)])\s+(.*)$")
_HEADING_LINE_RE = re.compile(r"^ {0,3}#{1,6}\s+(.*?)(?:\s+#+)?\s*$")
_QUOTE_LINE_RE = re.compile(r"^\s*>\s?(.*)$")
_RULE_LINE_RE = re.compile(r"^\s*(?:([-*_])\s*){3,}$")
_TABLE_SEPARATOR_RE = re.compile(r"^\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?$")


class _NotSimple(Exception):
    """Raised inside the renderer when a block needs the model."""


def _as_sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    # A trailing colon introduces what follows ("Here is the plan:") and is
    # kept; anything else unterminated gets a full stop so the synthesiser
    # pauses between items instead of running them together.
    if text[-1] in ".!?:;":
        return text
    return text.rstrip(",") + "."


def _is_table_line(line: str) -> bool:
    return line.count("|") >= 2


def _table_cells(line: str) -> List[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _render_table(rows: List[str]) -> List[str]:
    """Reads a two-column table as "key: value." lines; header is dropped."""
    if len(rows) < 3 or not _TABLE_SEPARATOR_RE.match(rows[1].strip()):
        raise _NotSimple
    body = rows[2:]
    if len(body) > FAST_PATH_MAX_TABLE_ROWS:
        raise _NotSimple
    if any(len(_table_cells(r)) != 2 for r in rows):
        raise _NotSimple
    sentences = []
    for row in body:
        key, value = _table_cells(row)
        if not key and not value:
            continue
        sentences.append(_as_sentence(f"{key}: {value}" if key and value else key or value))
    return sentences


def render_simple_structure(text: str) -> Optional[str]:
    """Renders simply structured text as speakable prose, or None if it needs the model.

    Inline markdown (bold, italics, inline code, links) is left in place for
    `TextSanitizer`, which already reduces it to its words; this pass only
    deals with line structure.
    """
    if not text or not text.strip():
        return None
    if _COMPLEX_RE.search(text):
        return None

    lines = text.split("\n")
    sentences: List[str] = []
    list_items: List[str] = []

    def flush_list():
        sentences.extend(list_items)
        list_items.clear()

    try:
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if not stripped:
                flush_list()
                i += 1
                continue

            if _is_table_line(stripped):
                flush_list()
                rows = []
                while i < len(lines) and _is_table_line(lines[i].strip()):
                    rows.append(lines[i])
                    i += 1
                sentences.extend(_render_table(rows))
                continue

            i += 1

            if _RULE_LINE_RE.match(line):
                flush_list()
                continue

            heading = _HEADING_LINE_RE.match(line)
            if heading:
                flush_list()
                sentences.append(_as_sentence(heading.group(1)))
                continue

            item = _LIST_LINE_RE.match(line)
            if item:
                indent, marker, body = item.groups()
                body = body.strip()
                if len(indent.expandtabs(4)) >= 2:
                    raise _NotSimple  # nested list
                if not body or len(body) > FAST_PATH_MAX_ITEM_CHARS:
                    raise _NotSimple
                if marker[0].isdigit():
                    number = int(marker[:-1])
                    if not 1 <= number <= len(_ORDINALS):
                        raise _NotSimple
                    body = f"{_ORDINALS[number - 1]}, {body}"
                list_items.append(_as_sentence(body))
                if len(list_items) > FAST_PATH_MAX_ITEMS:
                    raise _NotSimple
                continue

            if list_items and line[:1] in (" ", "\t"):
                raise _NotSimple  # an item continued over several lines

            quote = _QUOTE_LINE_RE.match(line)
            if quote:
                flush_list()
                body = quote.group(1).strip()
                if body.startswith(">") or _LIST_LINE_RE.match(body) or _HEADING_LINE_RE.match(body):
                    raise _NotSimple  # structure nested inside a quote
                if body:
                    sentences.append(_as_sentence(body))
                continue

            flush_list()
            sentences.append(_as_sentence(stripped))
        flush_list()
    except _NotSimple:
        return None

    spoken = " ".join(s for s in sentences if s)
    return spoken or None
