"""The tickers market-scout screens: the bullets of pkm/wallet/stock/portfolio.md and watchlist.md,
or the ones the caller names.

The lists are plain markdown, one ticker per bullet:

    # Watchlist

    - XOM
    - CAT

A bullet counts when its first word is a ticker as written (upper case, e.g.
`BRK.B`, `^GSPC`), so "- **PEP** core holding", "- $IBM" and "- [[CAT]]"
all count, and "- Consider adding energy" doesn't. Headings, blank lines,
empty bullets and a frontmatter block are skipped. Every other line comes
back raw in the report, so a ticker written another way is reported, not
dropped.
"""
import os
import re
from typing import List, Optional, Tuple, Union

from core.tooling import Report, ToolError, display_path, pkm_path
from tools.yfinance import _normalize_tickers

LISTS = ("portfolio", "watchlist")

_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?(?P<text>.*)$")
_EMPTY_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s*$")
_FIRST_WORD = re.compile(r"[\s*_`\[($]*(?P<word>\^?[A-Za-z0-9][A-Za-z0-9.\-=]*)")
_SYMBOL = re.compile(r"^\^?[A-Z0-9][A-Z0-9.\-=]{0,14}$")
_HEADING = re.compile(r"^\s{0,3}#{1,6}(?:\s|$)")
_FRONTMATTER = "---"


def list_path(name: str) -> str:
    """The absolute path of a list: `list_path("watchlist")` is pkm/wallet/stock/watchlist.md."""
    return pkm_path("wallet", "stock", f"{name}.md")


def parse_list(text: str) -> Tuple[List[str], List[str]]:
    """(tickers, lines not recognized) for a list file's text. Tickers in order, each once."""
    lines = text.splitlines()
    symbols: List[str] = []
    unrecognized: List[str] = []
    for line in lines[_frontmatter_end(lines):]:
        if not line.strip() or _HEADING.match(line) or _EMPTY_BULLET.match(line):
            continue
        symbol = _symbol(line)
        if symbol is None:
            unrecognized.append(line)
        elif symbol not in symbols:
            symbols.append(symbol)
    return symbols, unrecognized


def followed_tickers(report: Report) -> List[Tuple[str, str]]:
    """(symbol, list) for every ticker in portfolio.md, then watchlist.md.

    A symbol on both lists is listed once, as "portfolio". Either list may be
    missing or empty, both even: no tickers, and no issue. In `report`: the
    lines no rule recognized (raw).
    """
    followed: List[Tuple[str, str]] = []
    seen = set()
    for name in LISTS:
        path = list_path(name)
        if not os.path.isfile(path):
            report.trace(f"{display_path(path)}: not found, so no tickers from it")
            continue
        with open(path, encoding="utf-8") as f:
            symbols, unrecognized = parse_list(f.read())
        report.unparsed(path, unrecognized)
        report.read(path, records=len(symbols), skipped=len(unrecognized))
        for symbol in symbols:
            if symbol not in seen:
                seen.add(symbol)
                followed.append((symbol, name))
    return followed


def requested_tickers(tickers: Union[List[str], str]) -> List[Tuple[str, None]]:
    """(symbol, None) for each symbol the caller named, upper case, each once.

    Raises ToolError when none is left.
    """
    symbols = _normalize_tickers(tickers)
    if not symbols:
        raise ToolError("'tickers' names no symbols: pass e.g. [\"KO\", \"PEP\"], or leave it out to screen the lists")
    return [(symbol, None) for symbol in symbols]


def _frontmatter_end(lines: List[str]) -> int:
    """The index of the first line after a leading `---` frontmatter block; 0 without one."""
    if lines and lines[0].strip() == _FRONTMATTER:
        for index in range(1, len(lines)):
            if lines[index].strip() == _FRONTMATTER:
                return index + 1
    return 0


def _symbol(line: str) -> Optional[str]:
    """The ticker a bullet names, or None when the line isn't a ticker bullet."""
    bullet = _BULLET.match(line)
    first = _FIRST_WORD.match(bullet.group("text")) if bullet else None
    if not first:
        return None
    word = first.group("word").rstrip(".-=")
    return word if _SYMBOL.match(word) and any(c.isalpha() for c in word) else None
