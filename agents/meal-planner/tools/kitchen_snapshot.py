"""kitchen_snapshot: what's in the kitchen and what the family ate lately, in one read."""
from core.tooling import Report, agent_tool, get_local_now, load_rules, rules_path

from agents.meal_planner.lib.inventory_doc import InventoryDoc
from agents.meal_planner.lib.kitchen_io import INVENTORY, MEAL_LOGS, STAPLES, kitchen_path, read_text
from agents.meal_planner.lib.kitchen_view import build_snapshot, staples_unrecognized, to_json
from agents.meal_planner.lib.meal_log import unrecognized
from agents.meal_planner.lib.rules import RULES
from agents.meal_planner.lib.shelf_life import kind_table_problems


@agent_tool
def kitchen_snapshot(report: Report) -> str:
    """Everything needed to plan meals or answer what's in the kitchen, in one read.

    Reads: pkm/wallet/kitchen/INVENTORY.md, STAPLES.md and MEAL_LOGS.md, and RULES.md for shelf lives.
    Computes: each row's days left, from its 保鮮至 or from 入庫日期 plus a shelf life ("est": true, rough); which list it goes in.
    Leaves out: what to cook, and whether an expired item is really gone. Rows with no date at all come back raw under issues.

    Returns JSON: today; expired, expiring_soon, perishables: {item, qty, days_left, est?}, soonest first,
    days_left negative once past; long_life by section; staples (always stocked); recent_meals, newest first,
    with days_ago; last_ideas. Item names are as kitchen_inventory expects them.
    """
    rules = load_rules("meal-planner", RULES, report)
    for problem in kind_table_problems(rules):
        report.problem(rules_path("meal-planner"), problem)
    today = get_local_now().date()
    inventory_path, logs_path, staples_path = (kitchen_path(name) for name in (INVENTORY, MEAL_LOGS, STAPLES))
    inventory = read_text(inventory_path)
    if inventory is None:
        report.missing(inventory_path, "file not found")
    report.raise_if_failed()
    meal_logs = _optional(logs_path, report)
    staples = _optional(staples_path, report)

    doc = InventoryDoc(inventory)
    snapshot = build_snapshot(doc, meal_logs, staples, today, rules)
    report.unparsed(inventory_path, doc.unrecognized())
    if snapshot.undated:
        count = len(snapshot.undated)
        issue = report.problem(inventory_path, f"{count} {'row has' if count == 1 else 'rows have'} no 入庫日期 "
                                               "and no date in the note, so how fresh they are is unknown")
        issue.lines.extend(snapshot.undated)
    meal_lines = unrecognized(meal_logs, today, rules["recent_meal_days"])
    report.unparsed(logs_path, meal_lines)
    staple_lines = staples_unrecognized(staples)
    report.unparsed(staples_path, staple_lines)

    data = snapshot.data
    report.read(inventory_path, records=len(doc.rows()), skipped=len(doc.unrecognized()) + len(snapshot.undated))
    if meal_logs:
        meals = sum(len(day["meals"]) for day in data["recent_meals"]) + len(data.get("last_ideas", {}).get("ideas", []))
        report.read(logs_path, records=meals, skipped=len(meal_lines))
    if staples:
        report.read(staples_path, records=len(data["staples"]), skipped=len(staple_lines))
    report.trace(f"lists: expired below 0 days left, expiring_soon up to {rules['expiring_soon_days']}, "
                 f"perishables up to {rules['perishable_days']}, long_life beyond; "
                 f"meals of the last {rules['recent_meal_days']} days")
    for line in snapshot.traces:
        report.trace(line)
    return to_json(data)


def _optional(path: str, report: Report) -> str:
    """The file's text; "" when it's missing, which the report notes."""
    text = read_text(path)
    if text is None:
        report.problem(path, "file not found")
        return ""
    return text
