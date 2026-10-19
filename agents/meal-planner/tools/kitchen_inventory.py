"""kitchen_inventory: change the kitchen inventory and log meals and dinner ideas, in one call."""
from contextlib import ExitStack
from datetime import timedelta
from typing import List, Literal, Optional

from core.tooling import (
    PermissionDenied, Report, ToolError, agent_tool, current_agent_id, display_path, get_local_now,
    has_permission, load_rules, rules_path,
)

from agents.meal_planner.lib.inventory_doc import InventoryDoc
from agents.meal_planner.lib.inventory_ops import NOT_FOUND, KitchenItem, apply
from agents.meal_planner.lib.kitchen_io import INVENTORY, MEAL_LOGS, kitchen_path, locked, read_text, write_atomic
from agents.meal_planner.lib.meal_log import MealIdea, MealLog, log_ideas, log_meal
from agents.meal_planner.lib.rules import RULES
from agents.meal_planner.lib.shelf_life import kind_table_problems

# Every op writes (the inventory ops INVENTORY.md, "log" MEAL_LOGS.md), so a
# read-only run (the voice replay) refuses them all.
READ_ACTIONS = ()
LOG_ACTION = "log"


@agent_tool
def kitchen_inventory(
    report: Report,
    op: Literal["use", "add", "remove", "set", "log"],
    items: Optional[List[KitchenItem]] = None,
    meal: Optional[MealLog] = None,
    ideas: Optional[List[MealIdea]] = None,
) -> dict:
    """Change the kitchen inventory (INVENTORY.md) and log meals and dinner ideas (MEAL_LOGS.md) in one call.

    Reads: pkm/wallet/kitchen/INVENTORY.md and MEAL_LOGS.md, which it rewrites, and RULES.md for shelf lives.
    Computes: what's left after use (a row at 0 is removed; rows sharing a name go oldest first); new rows; 保鮮至 from a stated date or shelf life.
    Leaves out: what a dish used and what's gone: you name the items. Items it can't apply are listed under issues; the rest still apply.

    Returns only what changed, with the arithmetic done: relay it, don't recompute.

    Args:
        op: use (deduct what was eaten), add (purchases), remove (finished, spoiled or gone), set (correct a row), or log (only meal and ideas).
        items: The rows to change; not needed for log.
        meal: A meal to log under its date, with what use deducted; goes with any op.
        ideas: Dinner ideas being proposed, logged under today; go with any op.
    """
    items = items or []
    ideas = [idea for idea in ideas or [] if any(d.strip() for d in idea.dishes)]
    if op != "log" and not items:
        raise ToolError(f"op '{op}' needs items.")
    if meal is not None and not meal.dishes.strip():
        raise ToolError("'meal' needs its dishes.")
    if op == "log" and meal is None and not ideas:
        raise ToolError("op 'log' needs a meal or ideas with dishes.")

    inventory_path, logs_path = kitchen_path(INVENTORY), kitchen_path(MEAL_LOGS)
    writes = [(op, inventory_path)] if op != "log" else []
    if meal is not None or ideas:
        writes.append((LOG_ACTION, logs_path))
    refused = [f"'{action}' {display_path(path)}" for action, path in writes if not has_permission(action, path)]
    if refused:  # checked before anything is written, so a refusal never leaves half a check-in
        raise PermissionDenied(f"Agent '{current_agent_id()}' does not have permission to {' or '.join(refused)}.")

    today = get_local_now().date()
    payload = {}
    with ExitStack() as stack:
        texts = {}
        for _, path in writes:
            stack.enter_context(locked(path))
            texts[path] = read_text(path)
            if texts[path] is None:
                report.missing(path, "file not found")
        report.raise_if_failed()  # a missing file writes nothing

        consumed = []
        if op != "log":
            rules = load_rules("meal-planner", RULES, report)
            for problem in kind_table_problems(rules):
                report.problem(rules_path("meal-planner"), problem)
            doc = InventoryDoc(texts[inventory_path])
            report.read(inventory_path, records=len(doc.rows()), skipped=len(doc.unrecognized()))
            result = apply(doc, op, items, today, rules)
            for name, why in result.errors:
                report.problem(name, why)
            if any(why == NOT_FOUND for _, why in result.errors):
                report.unparsed(inventory_path, doc.unrecognized())  # the item may be on a line it can't read
            for line in result.traces:
                report.trace(line)
            updated = doc.render()
            if updated != texts[inventory_path]:
                write_atomic(inventory_path, updated)
            payload["changed"] = result.changed
            consumed = result.consumed

        if meal is not None or ideas:
            text, logged = texts[logs_path], []
            report.read(logs_path)
            if meal is not None:
                day = today - timedelta(days=meal.days_ago)
                text = log_meal(text, day, meal.label, meal.dishes, consumed)
                logged.append(f"{day.isoformat()} {meal.label}")
            if ideas:
                text = log_ideas(text, today, ideas)
                logged.append(f"{today.isoformat()} 晚餐提案 ×{len(ideas)}")
            write_atomic(logs_path, text)
            payload["logged"] = logged
    return payload
