"""The inventory changes kitchen_inventory makes: use, add, remove and set.

Each op applies item by item to an InventoryDoc and reports only the rows it
changed, with the arithmetic done: portions deducted (fractions too), a row
that reaches zero removed, expiry dates computed from the purchase date. An
item that can't be applied is reported and the rest still apply.

Names match the row's whole name, then any of its parts (Chinese, English, or
a '/' alternative), then a piece of it. Rows sharing a name are batches of one
item, taken oldest first: the one expiring soonest.

A new row's 保鮮至 is written only from a date or shelf life the item states
(`expires`, `shelf_life_days`); otherwise its expiry is estimated from its
入庫日期 whenever it's read (lib/shelf_life.py).
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, List, Mapping, Optional, Tuple

from pydantic import BaseModel, Field

from agents.meal_planner.lib.inventory_doc import (
    ADDED, NOTE, QTY, InventoryDoc, Row, chinese_name, name_aliases, normalize,
)
from agents.meal_planner.lib.quantity import (
    EPSILON, PORTION, deduct, fmt, has_unit, parse_amount, render, set_portions,
)
from agents.meal_planner.lib.shelf_life import expiry, parse_date, with_expiry

OPS = ("use", "add", "remove", "set")
NOT_FOUND = "not in the inventory"
# A piece of a name shorter than this matches too much (蛋 is in every egg dish).
_MIN_PARTIAL = 2


class KitchenItem(BaseModel):
    """One inventory row. Which fields apply depends on the op."""

    name: str = Field(description=(
        "As kitchen_snapshot shows it; a Chinese or English part also matches. For add: "
        "Traditional Chinese with the English in parentheses, e.g. '牛肉 (Beef)'."))
    portions: Optional[float] = Field(None, gt=0, description=(
        "One portion is one family meal; fractions like 0.5 are fine. use: eaten; add/set: covered."))
    qty: Optional[str] = Field(None, description=(
        "An amount with its unit. use: in a unit the row counts, e.g. '4 顆', instead of "
        "portions; add/set: the count, e.g. '1 盒'."))
    section: Optional[str] = Field(None, description="add: the section by its Chinese or English name, e.g. 'Meats', '水果'.")
    added: Optional[date] = Field(None, description="Purchase date (入庫日期), YYYY-MM-DD; add defaults to today.")
    shelf_life_days: Optional[int] = Field(None, ge=0, description=(
        "Only when the user says how long it keeps: days from the purchase date, written as 保鮮至. Never a guess."))
    expires: Optional[date] = Field(None, description=(
        "Only a date printed on the package or given by the user, YYYY-MM-DD, written as 保鮮至. "
        "Without it, expiry is estimated from the purchase date."))
    note: Optional[str] = Field(None, description=(
        "Storage note in Traditional Chinese, e.g. '冷藏保存', '冷凍保存': the expiry estimate reads it."))


class ItemError(ValueError):
    """An item that can't be applied; the message says why."""


@dataclass
class OpResult:
    changed: List[dict] = field(default_factory=list)  # one entry per changed row
    consumed: List[str] = field(default_factory=list)  # what `use` took, worded for MEAL_LOGS.md
    errors: List[Tuple[str, str]] = field(default_factory=list)  # (item name as given, why it wasn't applied)
    traces: List[str] = field(default_factory=list)  # the arithmetic behind each change, for explain mode


def apply(doc: InventoryDoc, op: str, items: List[KitchenItem], today: date, rules: Mapping[str, Any]) -> OpResult:
    """Applies `op` to every item, in order. `rules` are the RULES.md values (lib/rules.py)."""
    if op not in OPS:
        raise ValueError(f"unknown op '{op}' (expected one of {', '.join(OPS)})")
    handler = {"use": _use, "add": _add, "remove": _remove, "set": _set}[op]
    result = OpResult()
    for item in items:
        try:
            handler(doc, item, today, rules, result)
        except ItemError as e:
            result.errors.append((item.name, str(e)))
    return result


def days_left(row: Row, today: date, rules: Mapping[str, Any]) -> dict:
    """{"days_left": N} for a row with a known or estimated expiry, plus "est" if estimated."""
    found = expiry(row.section, row.name, row.note, parse_date(row.added), rules)
    if found.on is None:
        return {}
    return {"days_left": found.days_left(today), **({"est": True} if found.estimated else {})}


def match(doc: InventoryDoc, name: str, rules: Mapping[str, Any]) -> List[Row]:
    """The rows `name` refers to, the batch to take first leading. Raises ItemError
    when nothing matches, or when only pieces of several different names do."""
    rows = doc.rows()
    wanted, aliases = normalize(name), name_aliases(name)
    found = [r for r in rows if normalize(r.name) == wanted]
    found = found or [r for r in rows if aliases & name_aliases(r.name)]
    if not found:
        found = [r for r in rows if any(len(a) >= _MIN_PARTIAL and a in normalize(r.name) for a in aliases)]
        names = sorted({r.name for r in found})
        if len(names) > 1:
            raise ItemError(f"matches several items ({'; '.join(names)}); use the exact name")
    if not found:
        raise ItemError(NOT_FOUND)
    position = {id(r): i for i, r in enumerate(rows)}

    def oldest_first(row):
        expires = expiry(row.section, row.name, row.note, parse_date(row.added), rules).on
        return (expires or date.max, parse_date(row.added) or date.max, position[id(row)])

    return sorted(found, key=oldest_first)


def _use(doc: InventoryDoc, item: KitchenItem, today: date, rules: Mapping[str, Any], result: OpResult) -> None:
    if item.qty:
        try:
            amount, unit = parse_amount(item.qty)
        except ValueError as e:
            raise ItemError(str(e)) from None
    else:
        amount, unit = item.portions or 1.0, PORTION
    rows = match(doc, item.name, rules)
    counted = [r for r in rows if has_unit(r.qty, unit)]
    if not counted:
        raise ItemError(f"'{rows[0].qty}' isn't counted in {unit}; give qty in its own unit, or set it")
    wanted = amount
    for row in counted:  # oldest batch first; the rest only if the request is larger
        before = row.qty
        taken = deduct(row.qty, wanted, unit)
        entry = {"item": row.name, "used": f"{fmt(taken.taken)} {unit}"}
        if taken.text is None:
            doc.remove(row)
            entry["removed"] = True
            result.consumed.append(f"{chinese_name(row.name)}（消耗 {fmt(taken.taken)} {unit}，完食扣除）")
            result.traces.append(f"{row.name}: {before} - {fmt(taken.taken)} {unit} = nothing left; row removed")
        else:
            row.set(QTY, taken.text)
            entry["left"] = taken.text
            result.consumed.append(f"{chinese_name(row.name)}（消耗 {fmt(taken.taken)} {unit}，剩餘 {taken.text}）")
            result.traces.append(f"{row.name}: {before} - {fmt(taken.taken)} {unit} = {taken.text}")
        result.changed.append(entry)
        wanted -= taken.taken
        if wanted <= EPSILON:
            break


def _add(doc: InventoryDoc, item: KitchenItem, today: date, rules: Mapping[str, Any], result: OpResult) -> None:
    if not item.name.strip():
        raise ItemError("add needs a name")
    if not (item.section or "").strip():
        raise ItemError("add needs a section, e.g. 'Meats'")
    added = item.added or today
    qty = render(item.qty, item.portions, rules["default_portions"])
    note = item.note or ""
    expires = _expires(item, added)
    if expires:
        note = with_expiry(note, expires)
    row, heading, created = doc.insert(item.section, [item.name, qty, added.isoformat(), note])
    entry = {"item": row.name, "qty": row.qty, "section": heading, **days_left(row, today, rules)}
    if created:
        entry["new_section"] = True
    result.changed.append(entry)
    result.traces.append(_expiry_trace(row, today, rules))


def _remove(doc: InventoryDoc, item: KitchenItem, today: date, rules: Mapping[str, Any], result: OpResult) -> None:
    row = match(doc, item.name, rules)[0]
    doc.remove(row)
    result.changed.append({"item": row.name, "removed": True, "was": row.qty})


def _set(doc: InventoryDoc, item: KitchenItem, today: date, rules: Mapping[str, Any], result: OpResult) -> None:
    row = match(doc, item.name, rules)[0]
    kept_expiry = parse_date(row.note)
    if item.qty is not None:
        row.set(QTY, render(item.qty, item.portions, rules["default_portions"]))
    elif item.portions is not None:
        row.set(QTY, set_portions(row.qty, item.portions))
    if item.added is not None:
        row.set(ADDED, item.added.isoformat())
    if item.note is not None:
        row.set(NOTE, with_expiry(item.note, kept_expiry) if kept_expiry else item.note)
    expires = _expires(item, parse_date(row.added) or today)
    if expires:
        row.set(NOTE, with_expiry(row.note, expires))
    if not row.dirty:
        raise ItemError("nothing to change: give qty, portions, added, shelf_life_days, expires or note")
    result.changed.append({"item": row.name, "qty": row.qty, "added": row.added, "note": row.note,
                           **days_left(row, today, rules)})
    result.traces.append(_expiry_trace(row, today, rules))


def _expires(item: KitchenItem, added: date) -> Optional[date]:
    """The 保鮮至 to write: a stated date, or a stated shelf life from `added`; None when neither is given."""
    if item.expires:
        return item.expires
    if item.shelf_life_days is not None:
        return added + timedelta(days=item.shelf_life_days)
    return None


def _expiry_trace(row: Row, today: date, rules: Mapping[str, Any]) -> str:
    found = expiry(row.section, row.name, row.note, parse_date(row.added), rules)
    left = found.days_left(today)
    return f"{row.name}: {found.explain()}" + (f"; days_left {left}" if left is not None else "")
