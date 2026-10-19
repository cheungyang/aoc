import unittest

from tests.agents.meal_planner import INVENTORY
from agents.meal_planner.lib.inventory_doc import InventoryDoc, chinese_name, clean_cell, name_aliases


class TestRoundTrip(unittest.TestCase):

    def test_an_untouched_document_renders_byte_for_byte(self):
        self.assertEqual(InventoryDoc(INVENTORY).render(), INVENTORY)

    def test_crlf_and_a_missing_final_newline_survive(self):
        text = INVENTORY.replace("\n", "\r\n").rstrip("\r\n")
        self.assertEqual(InventoryDoc(text).render(), text)

    def test_rows_know_their_section_and_cells(self):
        rows = InventoryDoc(INVENTORY).rows()
        self.assertEqual((rows[0].section, rows[0].name, rows[0].qty, rows[0].added, rows[0].note),
                         ("肉類 (Meats)", "牛肉 (Beef)", "1 份", "2026-09-20", "冷藏/冷凍保存"))
        odd = next(r for r in rows if r.name.startswith("蔥油餅"))
        self.assertEqual((odd.name, odd.qty), ("蔥油餅 (Scallion pancakes)", "1 包"))

    def test_header_and_separator_lines_are_not_rows(self):
        self.assertNotIn("食材名稱", [r.name for r in InventoryDoc(INVENTORY).rows()])


class TestUnrecognized(unittest.TestCase):

    def test_titles_notes_headings_tables_and_blank_lines_are_the_files_structure(self):
        self.assertEqual(InventoryDoc(INVENTORY).unrecognized(), [])
        self.assertEqual(InventoryDoc("# 庫存\n\n---\n<!-- 註 -->\n> 註\n").unrecognized(), [])

    def test_other_lines_are_listed_as_written_and_kept(self):
        text = INVENTORY + (
            "- 青花菜 (Broccoli) 1 份\n"          # a bullet typed by hand
            "| 無分隔 | 1 份 |\n"                   # a table without its separator line
            "\n### 海鮮 (Seafood)\n| 食材名稱 | 剩餘份量 | 入庫日期 | 備註 |\n| :--- | :--- | :--- | :--- |\n"
            "|  | 2 份 | 2026-10-01 |  |\n"       # a row with no name
            "|  |  |  |  |\n"                       # an empty row: nothing to read
        )
        doc = InventoryDoc(text)
        self.assertEqual(doc.unrecognized(), ["- 青花菜 (Broccoli) 1 份", "| 無分隔 | 1 份 |", "|  | 2 份 | 2026-10-01 |  |"])
        self.assertNotIn("", [r.name for r in doc.rows()])
        self.assertEqual(doc.render(), text)


class TestEdits(unittest.TestCase):

    def test_only_the_changed_row_is_rewritten(self):
        doc = InventoryDoc(INVENTORY)
        row = next(r for r in doc.rows() if r.name.startswith("蔥油餅"))
        row.set(1, "2 包")
        self.assertEqual(
            doc.render(),
            INVENTORY.replace("| 蔥油餅 (Scallion pancakes)| 1 包 |",
                              "| 蔥油餅 (Scallion pancakes) | 2 包 |"),
        )

    def test_setting_the_same_value_changes_nothing(self):
        doc = InventoryDoc(INVENTORY)
        doc.rows()[0].set(1, "1 份")
        self.assertEqual(doc.render(), INVENTORY)

    def test_remove_deletes_one_line(self):
        doc = InventoryDoc(INVENTORY)
        doc.remove(doc.rows()[0])
        self.assertEqual(doc.render(), INVENTORY.replace("| 牛肉 (Beef) | 1 份 | 2026-09-20 | 冷藏/冷凍保存 |\n", ""))

    def test_insert_goes_after_the_section_table(self):
        doc = InventoryDoc(INVENTORY)
        row, heading, created = doc.insert("Meats", ["雞腿 (Chicken thighs)", "2 份", "2026-10-16", "冷藏保存"])
        self.assertEqual((heading, created), ("肉類 (Meats)", False))
        self.assertEqual(
            doc.render(),
            INVENTORY.replace(
                "| 豬排骨 (Pork ribs) | 1 盒 (2 份) | 2026-10-11 | 冷藏/冷凍保存，燉湯用 |\n",
                "| 豬排骨 (Pork ribs) | 1 盒 (2 份) | 2026-10-11 | 冷藏/冷凍保存，燉湯用 |\n"
                "| 雞腿 (Chicken thighs) | 2 份 | 2026-10-16 | 冷藏保存 |\n"),
        )

    def test_insert_into_the_last_section(self):
        doc = InventoryDoc(INVENTORY)
        doc.insert("水果", ["藍莓 (Blueberries)", "1 盒", "2026-10-16", "冷藏保存"])
        self.assertTrue(doc.render().endswith(
            "| 鳳梨 (Pineapple) | 1 顆 | 2026-10-11 | 常溫/冷藏 |\n| 藍莓 (Blueberries) | 1 盒 | 2026-10-16 | 冷藏保存 |\n"))

    def test_an_unknown_section_is_created_at_the_end_with_the_same_header(self):
        doc = InventoryDoc(INVENTORY)
        _, heading, created = doc.insert("海鮮 (Seafood)", ["鮭魚 (Salmon)", "2 份", "2026-10-16", "冷凍保存"])
        self.assertEqual((heading, created), ("海鮮 (Seafood)", True))
        self.assertTrue(doc.render().endswith(
            "| 鳳梨 (Pineapple) | 1 顆 | 2026-10-11 | 常溫/冷藏 |\n\n### 海鮮 (Seafood)\n"
            "| 食材名稱 | 剩餘份量 / 數量 (以份計) | 入庫日期 | 預計保鮮期 / 備註 |\n"
            "| :--- | :--- | :--- | :--- |\n| 鮭魚 (Salmon) | 2 份 | 2026-10-16 | 冷凍保存 |\n"))

    def test_a_heading_without_a_table_gets_one(self):
        doc = InventoryDoc("# 廚房庫存\n\n### 水果 (Fruits)\n\n### 肉類 (Meats)\n")
        doc.insert("Fruits", ["草莓 (Strawberries)", "1 盒", "2026-10-16", ""])
        self.assertEqual(doc.render(), (
            "# 廚房庫存\n\n### 水果 (Fruits)\n| 食材名稱 | 剩餘份量 / 數量 | 入庫日期 | 預計保鮮期 / 備註 |\n"
            "| :--- | :--- | :--- | :--- |\n| 草莓 (Strawberries) | 1 盒 | 2026-10-16 |  |\n\n### 肉類 (Meats)\n"))

    def test_an_empty_file_gets_a_title_and_a_section(self):
        doc = InventoryDoc("")
        doc.insert("水果 (Fruits)", ["草莓 (Strawberries)", "1 盒", "2026-10-16", "冷藏保存"])
        self.assertTrue(doc.render().startswith("# 廚房庫存 (Kitchen Inventory)\n\n### 水果 (Fruits)\n"))

    def test_find_section(self):
        doc = InventoryDoc(INVENTORY)
        self.assertEqual(doc.find_section("vegetables"), "蔬菜與鮮蔬 (Fresh Vegetables)")
        self.assertEqual(doc.find_section("Dairy"), "蛋品、豆漿與乳製品 (Eggs, Soy Milk & Dairy)")
        self.assertEqual(doc.find_section("Meats (肉類)"), "肉類 (Meats)")
        self.assertIsNone(doc.find_section("Seafood"))


class TestNames(unittest.TestCase):

    def test_aliases(self):
        self.assertEqual(name_aliases("西洋芹 / 芹菜 (Celery)"),
                         {"西洋芹 / 芹菜 (celery)", "西洋芹 / 芹菜", "西洋芹", "芹菜", "celery"})
        self.assertIn("eggs", name_aliases("放牧雞蛋（Eggs）"))

    def test_chinese_name(self):
        self.assertEqual(chinese_name("羊肉 (Lamb)"), "羊肉")
        self.assertEqual(chinese_name("1L 全脂鮮乳 (Whole milk)"), "1L 全脂鮮乳")
        self.assertEqual(chinese_name("草莓"), "草莓")

    def test_cells_stay_one_line_without_pipes(self):
        self.assertEqual(clean_cell("a | b\nc"), "a ／ b c")


if __name__ == "__main__":
    unittest.main()
