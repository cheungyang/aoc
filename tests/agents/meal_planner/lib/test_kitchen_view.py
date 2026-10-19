import json
import unittest

from tests.agents.meal_planner import INVENTORY, MEAL_LOGS, STAPLES, TODAY
from agents.meal_planner.lib.inventory_doc import InventoryDoc
from agents.meal_planner.lib.kitchen_view import bucket, build_snapshot, staples_list, staples_unrecognized, to_json
from agents.meal_planner.lib.rules import defaults


def snapshot_of(inventory=INVENTORY, meal_logs=MEAL_LOGS, staples=STAPLES, rules=None):
    return build_snapshot(InventoryDoc(inventory), meal_logs, staples, TODAY, rules or defaults())


class TestSnapshot(unittest.TestCase):

    def setUp(self):
        self.snapshot = snapshot_of().data

    def items(self, key):
        return [(e["item"], e["days_left"]) for e in self.snapshot[key]]

    def test_perishables_are_bucketed_and_sorted_by_days_left(self):
        self.assertEqual(self.items("expired"), [("金針菇 (Enoki mushroom)", -13), ("全脂鮮乳 (Whole milk)", -10)])
        self.assertEqual(self.items("expiring_soon"), [
            ("草莓 (Strawberries)", 2), ("鳳梨 (Pineapple)", 2), ("西洋芹 / 芹菜 (Celery)", 3),
            ("小黃瓜 (Cucumbers)", 3)])
        self.assertEqual(self.items("perishables")[:2], [("嫩豆腐 (Silken tofu)", 4), ("有機全脂鮮乳 (Whole milk)", 7)])

    def test_rows_carry_their_quantity_and_whether_the_date_is_estimated(self):
        self.assertEqual(self.snapshot["expiring_soon"][0],
                         {"item": "草莓 (Strawberries)", "qty": "1 盒", "days_left": 2, "est": True})
        self.assertEqual(self.snapshot["expiring_soon"][3], {"item": "小黃瓜 (Cucumbers)", "qty": "3 條", "days_left": 3})

    def test_a_written_expiry_is_used_as_is(self):
        text = INVENTORY.replace("| 2026-10-13 | 冷藏保存 |\n| 鳳梨", "| 2026-10-13 | 冷藏保存，保鮮至 2026-10-30 |\n| 鳳梨")
        snapshot = snapshot_of(text, "", "").data
        strawberries = next(e for e in snapshot["perishables"] if e["item"] == "草莓 (Strawberries)")
        self.assertEqual(strawberries, {"item": "草莓 (Strawberries)", "qty": "1 盒", "days_left": 14})

    def test_long_life_items_are_listed_by_section(self):
        long_life = self.snapshot["long_life"]
        self.assertEqual(long_life["肉類 (Meats)"][:2], ["牛肉 (Beef) 1 份", "羊肉 (Lamb) 0.5 份"])
        self.assertIn("蠔油 (Oyster sauce) 1 瓶", long_life["醃漬小菜與醬料 (Pickles & Sauces)"])
        self.assertIn("放牧雞蛋 (Eggs) 24 顆 (2 打)", long_life["蛋品、豆漿與乳製品 (Eggs, Soy Milk & Dairy)"])

    def test_every_row_appears_once(self):
        listed = [e["item"] for key in ("expired", "expiring_soon", "perishables") for e in self.snapshot[key]]
        listed += [entry for rows in self.snapshot["long_life"].values() for entry in rows]
        self.assertEqual(len(listed), 16)

    def test_a_row_with_no_date_at_all_comes_back_raw_instead_of_being_listed(self):
        text = INVENTORY.replace("| 牛肉 (Beef) | 1 份 | 2026-09-20 |", "| 牛肉 (Beef) | 1 份 |  |")
        snapshot = snapshot_of(text)
        self.assertEqual(snapshot.undated, ["| 牛肉 (Beef) | 1 份 |  | 冷藏/冷凍保存 |"])
        self.assertNotIn("牛肉 (Beef) 1 份", snapshot.data["long_life"]["肉類 (Meats)"])

    def test_the_lists_follow_the_rules(self):
        rules = defaults()
        rules.update(expiring_soon_days=5, perishable_days=10, recent_meal_days=4)
        snapshot = snapshot_of(rules=rules).data
        self.assertIn(("嫩豆腐 (Silken tofu)", 4), [(e["item"], e["days_left"]) for e in snapshot["expiring_soon"]])
        self.assertTrue(all(e["days_left"] <= 10 for e in snapshot["perishables"]))
        self.assertEqual([m["date"] for m in snapshot["recent_meals"]], ["2026-10-13"])
        self.assertNotIn("last_ideas", snapshot)  # 2026-10-12 is outside 10-13..10-16

    def test_bucket(self):
        rules = defaults()
        self.assertEqual([bucket(n, rules) for n in (-1, 0, 3, 4, 30, 31)],
                         ["expired", "expiring_soon", "expiring_soon", "perishables", "perishables", "long_life"])

    def test_traces_say_how_each_number_was_reached(self):
        traces = snapshot_of().traces
        self.assertIn("草莓 (Strawberries): 入庫日期 2026-10-13 + 5 days "
                      "(shelf_life_days: 莓 / berr / 香蕉 / banana / 酪梨 / avocado) = 2026-10-18; days_left 2", traces)
        self.assertEqual(len(traces), 16)

    def test_staples_meals_and_ideas(self):
        self.assertEqual(self.snapshot["staples"], ["醬油 (Soy Sauce)", "米 (Rice)", "食用油 (Cooking Oil)"])
        self.assertEqual(self.snapshot["recent_meals"][0]["date"], "2026-10-13")
        self.assertEqual(self.snapshot["last_ideas"]["date"], "2026-10-12")

    def test_empty_files_give_empty_lists(self):
        self.assertEqual(snapshot_of("", "", "").data, {
            "today": "2026-10-16", "expired": [], "expiring_soon": [], "perishables": [],
            "long_life": {}, "staples": [], "recent_meals": []})

    def test_staples_list_reads_bullets_only(self):
        self.assertEqual(staples_list("# 常備\n- 鹽 (Salt)\n* 糖 (Sugar)\n-\nnote"), ["鹽 (Salt)", "糖 (Sugar)"])

    def test_staples_lines_it_cannot_read(self):
        self.assertEqual(staples_unrecognized("# 常備\n- 鹽 (Salt)\n\n> 註\n糖 (Sugar)\n"), ["糖 (Sugar)"])

    def test_to_json_is_the_same_data_one_row_per_line(self):
        text = to_json(self.snapshot)
        self.assertEqual(json.loads(text), self.snapshot)
        self.assertIn('\n  {"item": "草莓 (Strawberries)", "qty": "1 盒", "days_left": 2, "est": true},\n', text)


if __name__ == "__main__":
    unittest.main()
