"""Quantities as INVENTORY.md writes them: '1 份', '1 盒 (2 份)', '約 1.5 lb (1 份)', '24 顆 (2 打)'.

A quantity is free text with numbers in it. Each number with the unit after it
is a term. A term in parentheses right after another restates it in a second
unit ('1 盒 (2 份)': one box, which is two portions), so the two scale together;
terms joined by '+' are separate amounts ('1 袋 + 0.8 lb'). Edits rewrite only
the numbers, so everything else ('約', spacing, units) stays as written.

One portion (份) is one family meal.
"""
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

PORTION = "份"
# English words a model may use for 份.
_PORTION_ALIASES = {"portion", "portions", "serving", "servings", "meal", "meals"}
# Less than this left counts as used up (float noise such as 0.1 + 0.2).
EPSILON = 1e-6

_TERM = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)(?:\s*/\s*(?P<den>\d+))?[ \t]*(?P<unit>[^\s\d()（）+＋,，、/／~～]*)"
)
_OPEN, _CLOSE = "(（", ")）"
_RESTATES = re.compile(r"\s*[(（]\s*")


@dataclass(frozen=True)
class Term:
    value: float
    unit: str
    start: int       # where the number starts in the text
    num_end: int     # where the number ends
    end: int         # where the unit ends
    in_parens: bool


@dataclass(frozen=True)
class Deduction:
    text: Optional[str]  # the quantity afterwards; None when the row is used up
    taken: float         # how much of the request this row covered
    left: float          # what's left in the deducted unit (0 when used up)


def fmt(value: float) -> str:
    """A number as the file writes it: at most two decimals, no trailing zeros."""
    text = f"{round(value, 2):.2f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def unit_key(unit: str) -> str:
    """The unit compared case-blind, with English portion words read as 份."""
    key = unit.strip().casefold()
    return PORTION if key in _PORTION_ALIASES else key


def parse_terms(text: str) -> List[Term]:
    """Every number in `text` with the unit written after it."""
    depths = _paren_depths(text)
    terms = []
    for m in _TERM.finditer(text):
        value = float(m.group("num"))
        num_end = m.end("num")
        if m.group("den"):
            if float(m.group("den")) == 0:
                continue
            value /= float(m.group("den"))
            num_end = m.end("den")
        terms.append(Term(value, m.group("unit"), m.start(), num_end, m.end(), depths[m.start()] > 0))
    return terms


def has_unit(text: str, unit: str) -> bool:
    key = unit_key(unit)
    return any(unit_key(t.unit) == key for t in parse_terms(text))


def portions(text: str) -> Optional[float]:
    """The portions a quantity counts, or None when it doesn't count 份."""
    for term in parse_terms(text):
        if unit_key(term.unit) == PORTION:
            return term.value
    return None


def parse_amount(text: str) -> Tuple[float, str]:
    """'4 顆' -> (4.0, '顆'); a bare number is portions. Raises ValueError if there's no amount."""
    terms = parse_terms(text or "")
    if not terms or terms[0].value <= 0:
        raise ValueError(f"'{text}' has no amount to deduct, e.g. '4 顆'")
    return terms[0].value, terms[0].unit or PORTION


def deduct(text: str, amount: float, unit: str = PORTION) -> Deduction:
    """`text` with `amount` of `unit` taken out; the term that restates it scales along.

    Raises ValueError when `text` has no term in `unit`.
    """
    terms = parse_terms(text)
    key = unit_key(unit)
    index = next((i for i, t in enumerate(terms) if unit_key(t.unit) == key), None)
    if index is None:
        raise ValueError(f"'{text}' isn't counted in {unit}")
    term = terms[index]
    left = term.value - amount
    if left <= EPSILON:
        return Deduction(None, term.value, 0.0)
    return Deduction(_rewrite(text, terms, _scaled(text, terms, index, left)), amount, left)


def set_portions(text: str, value: float) -> str:
    """`text` counting `value` portions: its 份 term rewritten (the restating term scales
    along), or ' (N 份)' appended when it had none."""
    terms = parse_terms(text)
    index = next((i for i, t in enumerate(terms) if unit_key(t.unit) == PORTION), None)
    if index is not None:
        return _rewrite(text, terms, _scaled(text, terms, index, value))
    base = text.strip()
    return f"{base} ({fmt(value)} {PORTION})" if base else f"{fmt(value)} {PORTION}"


def render(qty: Optional[str], portion_count: Optional[float], default_portions: float) -> str:
    """The quantity cell for a new row: '1 盒 (2 份)', '2 份' or '12 顆'.

    With neither an amount nor portions, the row covers `default_portions`
    (RULES.md `default_portions`).
    """
    qty = " ".join((qty or "").split())
    if qty and has_unit(qty, PORTION):
        return qty
    if qty and portion_count is not None:
        return f"{qty} ({fmt(portion_count)} {PORTION})"
    if qty:
        return qty
    return f"{fmt(default_portions if portion_count is None else portion_count)} {PORTION}"


def _paren_depths(text: str) -> List[int]:
    depths, depth = [], 0
    for ch in text:
        if ch in _OPEN:
            depth += 1
        depths.append(depth)
        if ch in _CLOSE:
            depth = max(0, depth - 1)
    return depths


def _partner(text: str, terms: List[Term], index: int) -> Optional[int]:
    """The term that restates terms[index] in another unit: 'N unit (M unit)'."""
    term = terms[index]
    if not term.in_parens and index + 1 < len(terms):
        nxt = terms[index + 1]
        if nxt.in_parens and _RESTATES.fullmatch(text[term.end:nxt.start]):
            return index + 1
    if term.in_parens and index > 0:
        prev = terms[index - 1]
        if not prev.in_parens and _RESTATES.fullmatch(text[prev.end:term.start]):
            return index - 1
    return None


def _scaled(text: str, terms: List[Term], index: int, value: float) -> Dict[int, float]:
    edits = {index: value}
    partner = _partner(text, terms, index)
    if partner is not None and terms[index].value > 0:
        edits[partner] = terms[partner].value * value / terms[index].value
    return edits


def _rewrite(text: str, terms: List[Term], edits: Dict[int, float]) -> str:
    for i in sorted(edits, reverse=True):
        term = terms[i]
        text = text[:term.start] + fmt(edits[i]) + text[term.num_end:]
    return text
