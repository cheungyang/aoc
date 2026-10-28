import unittest
from datetime import date

from tests.agents.meal_planner import INVENTORY, TODAY
from agents.meal_planner.lib.inventory_doc import InventoryDoc
from agents.meal_planner.lib.inventory_ops import KitchenItem, apply
from agents.meal_planner.lib.rules import defaults


def run(op, *items, text=INVENTORY, rules=None):
    doc = InventoryDoc(text)
    result = apply(doc, op, [KitchenItem(**item) for item in items], TODAY, rules or defaults())
    return doc, result


def row(doc, name):
    return next((r for r in doc.rows() if r.name == name), None)


class TestUse(unittest.TestCase):

    def test_one_portion_by_default_and_a_row_at_zero_is_removed(self):
        doc, result = run("use", {"name": "牛肉 (Beef)"})
        self.assertIsNone(row(doc, "牛肉 (Beef)"))
        self.assertEqual(result.changed, [{"item": "牛肉 (Beef)", "used": "1 份", "removed": True}])
        self.assertEqual(result.consumed, ["牛肉（消耗 1 份，完食扣除）"])
        self.assertEqual(result.traces, ["牛肉 (Beef): 1 份 - 1 份 = nothing left; row removed"])

    def test_fractions(self):
        doc, result = run("use", {"name": "豬排骨 (Pork ribs)", "portions": 0.5}, {"name": "羊肉 (Lamb)", "portions": 0.5})
        self.assertEqual(row(doc, "豬排骨 (Pork ribs)").qty, "0.75 盒 (1.5 份)")
        self.assertIsNone(row(doc, "羊肉 (Lamb)"))
        self.assertEqual(result.consumed, ["豬排骨（消耗 0.5 份，剩餘 0.75 盒 (1.5 份)）", "羊肉（消耗 0.5 份，完食扣除）"])
        self.assertEqual(result.traces[0], "豬排骨 (Pork ribs): 1 盒 (2 份) - 0.5 份 = 0.75 盒 (1.5 份)")

    def test_a_chinese_or_english_part_of_the_name_matches(self):
        doc, _ = run("use", {"name": "Celery", "portions": 0.5})
        self.assertEqual(row(doc, "西洋芹 / 芹菜 (Celery)").qty, "1.5 把 (1.5 份)")
        doc, _ = run("use", {"name": "芹菜", "portions": 0.5})
        self.assertEqual(row(doc, "西洋芹 / 芹菜 (Celery)").qty, "1.5 把 (1.5 份)")

    def test_another_unit(self):
        doc, result = run("use", {"name": "雞蛋", "qty": "4 顆"})
        self.assertEqual(row(doc, "放牧雞蛋 (Eggs)").qty, "20 顆 (1.67 打)")
        self.assertEqual(result.changed[0]["left"], "20 顆 (1.67 打)")

    def test_batches_sharing_a_name_go_oldest_first_and_spill_over(self):
        doc, result = run("use", {"name": "Whole milk", "qty": "2 瓶"})
        self.assertIsNone(row(doc, "全脂鮮乳 (Whole milk)"))
        self.assertEqual(row(doc, "有機全脂鮮乳 (Whole milk)").qty, "2 瓶")
        self.assertEqual([c["item"] for c in result.changed],
                         ["全脂鮮乳 (Whole milk)", "有機全脂鮮乳 (Whole milk)"])

    def test_a_row_not_counted_in_portions_is_left_alone_and_reported(self):
        doc, result = run("use", {"name": "黃洋蔥 (Brown onion)"}, {"name": "牛肉 (Beef)"})
        self.assertEqual(row(doc, "黃洋蔥 (Brown onion)").qty, "1 袋")
        self.assertIsNone(row(doc, "牛肉 (Beef)"))
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0][0], "黃洋蔥 (Brown onion)")
        self.assertIn("isn't counted in 份", result.errors[0][1])

    def test_unknown_and_ambiguous_names(self):
        _, result = run("use", {"name": "青花菜 (Broccoli)"}, {"name": "豆腐"})
        self.assertEqual(result.errors[0], ("青花菜 (Broccoli)", "not in the inventory"))
        self.assertIn("matches several items", result.errors[1][1])
        self.assertIn("嫩豆腐 (Silken tofu)", result.errors[1][1])

    def test_a_piece_of_one_name_is_enough(self):
        doc, _ = run("use", {"name": "tofu", "qty": "1 盒"})
        self.assertIsNone(row(doc, "嫩豆腐 (Silken tofu)"))
        self.assertIsNotNone(row(doc, "豆腐乳 (Fermented bean curd)"))


class TestAdd(unittest.TestCase):

    def test_a_purchase_with_a_stated_shelf_life_gets_its_expiry_date(self):
        doc, result = run("add", {"name": "藍莓 (Blueberries)", "qty": "1 盒", "section": "Fruits",
                                  "shelf_life_days": 7, "note": "冷藏保存"})
        self.assertIn("| 藍莓 (Blueberries) | 1 盒 | 2026-10-16 | 冷藏保存，保鮮至 2026-10-23 |\n", doc.render())
        self.assertEqual(result.changed, [{"item": "藍莓 (Blueberries)", "qty": "1 盒", "section": "水果 (Fruits)", "days_left": 7}])
        self.assertEqual(result.traces, ["藍莓 (Blueberries): 2026-10-23, written in the note; days_left 7"])

    def test_without_a_known_expiry_none_is_written_and_it_is_estimated(self):
        # 保鮮至 only when known; otherwise expiry counts from 入庫日期.
        doc, result = run("add", {"name": "藍莓 (Blueberries)", "qty": "1 盒", "section": "Fruits", "note": "冷藏保存"})
        self.assertIn("| 藍莓 (Blueberries) | 1 盒 | 2026-10-16 | 冷藏保存 |\n", doc.render())
        self.assertEqual(result.changed[0]["days_left"], 5)
        self.assertTrue(result.changed[0]["est"])

    def test_defaults_two_portions_and_today(self):
        doc, result = run("add", {"name": "雞腿 (Chicken thighs)", "section": "肉類", "note": "冷藏保存"})
        self.assertEqual((row(doc, "雞腿 (Chicken thighs)").qty, row(doc, "雞腿 (Chicken thighs)").added), ("2 份", "2026-10-16"))
        self.assertEqual(result.changed[0]["days_left"], 3)
        self.assertTrue(result.changed[0]["est"])

    def test_the_portions_default_comes_from_the_rules(self):
        rules = defaults()
        rules["default_portions"] = 1.5
        doc, _ = run("add", {"name": "雞腿 (Chicken thighs)", "section": "肉類"}, rules=rules)
        self.assertEqual(row(doc, "雞腿 (Chicken thighs)").qty, "1.5 份")

    def test_receipt_date_portions_and_printed_expiry(self):
        doc, _ = run("add", {"name": "牛奶 (Milk)", "qty": "1 瓶", "portions": 3, "section": "Dairy",
                             "added": date(2026, 10, 14), "expires": date(2026, 10, 25)})
        self.assertIn("| 牛奶 (Milk) | 1 瓶 (3 份) | 2026-10-14 | 保鮮至 2026-10-25 |", doc.render())

    def test_a_new_section(self):
        _, result = run("add", {"name": "鮭魚 (Salmon)", "section": "海鮮 (Seafood)", "shelf_life_days": 90})
        self.assertTrue(result.changed[0]["new_section"])

    def test_a_section_is_required(self):
        doc, result = run("add", {"name": "鮭魚 (Salmon)"})
        self.assertEqual(doc.render(), INVENTORY)
        self.assertIn("needs a section", result.errors[0][1])


class TestRemoveAndSet(unittest.TestCase):

    def test_remove(self):
        doc, result = run("remove", {"name": "全脂鮮乳 (Whole milk)"})
        self.assertIsNone(row(doc, "全脂鮮乳 (Whole milk)"))
        self.assertEqual(result.changed, [{"item": "全脂鮮乳 (Whole milk)", "removed": True, "was": "1 瓶"}])

    def test_remove_takes_the_oldest_batch(self):
        doc, _ = run("remove", {"name": "Whole milk"})
        self.assertIsNone(row(doc, "全脂鮮乳 (Whole milk)"))
        self.assertIsNotNone(row(doc, "有機全脂鮮乳 (Whole milk)"))

    def test_set_portions_scales_the_count(self):
        doc, _ = run("set", {"name": "豬排骨 (Pork ribs)", "portions": 1})
        self.assertEqual(row(doc, "豬排骨 (Pork ribs)").qty, "0.5 盒 (1 份)")

    def test_set_a_shelf_life_writes_the_date(self):
        doc, result = run("set", {"name": "草莓 (Strawberries)", "shelf_life_days": 2})
        self.assertEqual(row(doc, "草莓 (Strawberries)").note, "冷藏保存，保鮮至 2026-10-15")
        self.assertEqual(result.changed[0]["days_left"], -1)
        self.assertNotIn("est", result.changed[0])

    def test_a_new_note_keeps_the_written_expiry(self):
        text = INVENTORY.replace("| 2026-10-13 | 冷藏保存 |\n| 鳳梨", "| 2026-10-13 | 冷藏保存，保鮮至 2026-10-17 |\n| 鳳梨")
        doc, _ = run("set", {"name": "草莓 (Strawberries)", "note": "冷凍保存"}, text=text)
        self.assertEqual(row(doc, "草莓 (Strawberries)").note, "冷凍保存，保鮮至 2026-10-17")

    def test_set_without_changes_is_an_error(self):
        _, result = run("set", {"name": "草莓 (Strawberries)"})
        self.assertIn("nothing to change", result.errors[0][1])

    def test_unknown_op(self):
        with self.assertRaises(ValueError):
            apply(InventoryDoc(INVENTORY), "eat", [], TODAY, defaults())


if __name__ == "__main__":
    unittest.main()
