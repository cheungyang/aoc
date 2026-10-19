import unittest
from datetime import date

from agents.meal_planner.lib.rules import defaults
from agents.meal_planner.lib.shelf_life import (
    entry_pattern, estimate, expiry, kind_table_problems, parse_date, section_kind, with_expiry, written_expiry,
)

VEG = "蔬菜與鮮蔬 (Fresh Vegetables)"
MEAT = "肉類 (Meats)"
FRUIT = "水果 (Fruits)"
DAIRY = "蛋品、豆漿與乳製品 (Eggs, Soy Milk & Dairy)"
BAKERY = "冷冷凍麵點、烘焙與豆製品 (Frozen Staples, Bakery & Tofu/Soy)"
SAUCES = "醃漬小菜與醬料 (Pickles & Sauces)"
DRY = "辛香料、油脂與乾貨 (Aromatics, Fats & Dry Goods)"
RULES = defaults()


def days(section, name, note, rules=RULES):
    return estimate(section, name, note, rules)[0]


def with_rules(**changes):
    rules = defaults()
    for key, value in changes.items():
        if isinstance(value, dict):
            rules[key].update(value)
        else:
            rules[key] = value
    return rules


class TestWrittenExpiry(unittest.TestCase):

    def test_a_date_in_the_note(self):
        self.assertEqual(written_expiry("冷藏保存，保鮮至 2026-10-23", None), date(2026, 10, 23))

    def test_a_shelf_life_counts_from_the_purchase_date(self):
        added = date(2026, 10, 10)
        self.assertEqual(written_expiry("約 7 天", added), date(2026, 10, 17))
        self.assertEqual(written_expiry("可放 2 週", added), date(2026, 10, 24))
        self.assertEqual(written_expiry("3 個月", added), date(2027, 1, 8))

    def test_a_shelf_life_needs_a_purchase_date(self):
        self.assertIsNone(written_expiry("約 7 天", None))

    def test_a_storage_note_is_not_an_expiry(self):
        self.assertIsNone(written_expiry("冷藏/冷凍保存，適合煲湯", date(2026, 10, 1)))

    def test_parse_date(self):
        self.assertEqual(parse_date(" 2026-09-20 "), date(2026, 9, 20))
        self.assertIsNone(parse_date("2026-13-40"))
        self.assertIsNone(parse_date(""))


class TestEstimate(unittest.TestCase):

    def test_sauce_and_snack_sections_keep_for_months(self):
        self.assertEqual(days(SAUCES, "韓式泡菜 (Kimchi)", "冷藏保存"), 180)
        self.assertEqual(days(SAUCES, "豆腐乳 (Fermented bean curd)", "常溫/冷藏保存"), 180)

    def test_fridge_or_freezer_counts_as_frozen(self):
        self.assertEqual(days(MEAT, "牛肉 (Beef)", "冷藏/冷凍保存"), 90)
        self.assertEqual(days(BAKERY, "蔥油餅 (Scallion pancakes)", "冷冷凍保存"), 90)

    def test_dry_goods(self):
        self.assertEqual(days(DRY, "乾香菇 (Dried shiitake)", "常溫乾燥保存"), 180)
        self.assertEqual(days(FRUIT, "葡萄乾 (Raisins)", "常溫/冷藏"), 180)

    def test_items_that_keep_differently_from_their_section(self):
        self.assertEqual(days(DAIRY, "放牧雞蛋 (Eggs)", "冷藏保存"), 35)
        self.assertEqual(days(DAIRY, "皮蛋 (Century egg)", "常溫/冷藏保存"), 90)
        self.assertEqual(days(DAIRY, "鹹鴨蛋 (Salted eggs)", "常溫/冷藏保存"), 30)
        self.assertEqual(days(VEG, "黃洋蔥 (Brown onion)", "陰涼常溫保存"), 60)
        self.assertEqual(days(DRY, "青蔥 (Green onions)", "冷藏保存"), 7)
        self.assertEqual(days(FRUIT, "草莓 (Strawberries)", "冷藏保存"), 5)

    def test_rules_that_would_misfire_on_a_substring(self):
        # Bread before berries, scallion before onion, and pineapple is not an apple.
        self.assertEqual(days(BAKERY, "藍莓麵包 (Blueberry bread)", "常溫/冷藏保存"), 7)
        self.assertEqual(days(FRUIT, "鳳梨 (Pineapple)", "常溫/冷藏"), 7)
        self.assertEqual(days(FRUIT, "青蘋果 (Green apples)", "常溫/冷藏"), 45)

    def test_storage_defaults_by_section(self):
        self.assertEqual(days(MEAT, "雞腿 (Chicken thighs)", "冷藏保存"), 3)
        self.assertEqual(days(VEG, "金針菇 (Enoki mushroom)", "冷藏保存"), 7)
        self.assertEqual(days(BAKERY, "白麵條 (Wheat noodles)", "常溫保存"), 180)
        self.assertEqual(days(MEAT, "牛排 (Steak)", ""), 90)
        self.assertEqual(days("", "something", ""), 180)

    def test_it_names_the_rule_that_gave_the_days(self):
        self.assertEqual(estimate(FRUIT, "草莓 (Strawberries)", "冷藏保存", RULES),
                         (5, "shelf_life_days: 莓 / berr / 香蕉 / banana / 酪梨 / avocado"))
        self.assertEqual(estimate(MEAT, "雞腿 (Chicken thighs)", "冷藏保存", RULES), (3, "fridge_days: meat"))
        self.assertEqual(estimate(MEAT, "雞腿 (Chicken thighs)", "常溫", RULES), (180, "room_temp_days: other"))
        self.assertEqual(estimate(SAUCES, "蠔油 (Oyster sauce)", "", RULES), (180, "sauces_snacks_days"))


class TestRules(unittest.TestCase):
    """The days come from the RULES values; the defaults above are today's numbers."""

    def test_a_changed_value_applies(self):
        rules = with_rules(frozen_days=60, fridge_days={"meat": 4}, cool_dark_days=20)
        self.assertEqual(days(MEAT, "牛肉 (Beef)", "冷藏/冷凍保存", rules), 60)
        self.assertEqual(days(MEAT, "雞腿 (Chicken thighs)", "冷藏保存", rules), 4)
        self.assertEqual(days(VEG, "地瓜 (Sweet potato)", "陰涼保存", rules), 45)  # an item entry comes first
        self.assertEqual(days("雜項 (Misc)", "麵線 (Wheat vermicelli)", "陰涼保存", rules), 20)

    def test_a_changed_default_entry_keeps_its_place(self):
        rules = with_rules(shelf_life_days={"莓 / berr / 香蕉 / banana / 酪梨 / avocado": 3})
        self.assertEqual(days(FRUIT, "草莓 (Strawberries)", "冷藏保存", rules), 3)
        # Still after frozen: frozen berries keep frozen_days.
        self.assertEqual(days(FRUIT, "冷凍藍莓 (Frozen blueberries)", "冷凍保存", rules), 90)

    def test_an_added_entry_is_checked_before_everything_else(self):
        rules = with_rules(shelf_life_days={"草莓": 3, "泡菜 / kimchi": 30, "蔥油餅": 60})
        self.assertEqual(days(FRUIT, "草莓 (Strawberries)", "冷藏保存", rules), 3)  # before the berries entry
        self.assertEqual(days(SAUCES, "韓式泡菜 (Kimchi)", "冷藏保存", rules), 30)  # before the sauce section
        self.assertEqual(days(BAKERY, "蔥油餅 (Scallion pancakes)", "冷凍保存", rules), 60)  # before frozen
        self.assertEqual(days(FRUIT, "藍莓 (Blueberries)", "冷藏保存", rules), 5)  # other berries unchanged

    def test_names_split_by_a_slash_match_anywhere_and_quoted_ones_only_as_words(self):
        pattern = entry_pattern('雞蛋 / 鴨蛋 / "egg" / "eggs"')
        self.assertTrue(pattern.search("放牧雞蛋 (Eggs)"))
        self.assertTrue(pattern.search("Egg noodles"))
        self.assertFalse(pattern.search("茄子 (Eggplant)"))
        berries = entry_pattern("莓 / berr / 香蕉 / banana / 酪梨 / avocado")
        self.assertTrue(berries.search("Strawberries"))
        self.assertTrue(berries.search("Avocados"))
        self.assertTrue(entry_pattern("草莓 (Strawberries)").search("草莓 (Strawberries)"))  # parentheses are plain text
        self.assertTrue(entry_pattern("“apple”").search("Apple"))  # curly quotes work too
        self.assertIsNone(entry_pattern(" / "))

    def test_section_kinds(self):
        self.assertEqual([section_kind(s) for s in ("海鮮 (Seafood)", MEAT, FRUIT, VEG, DAIRY, BAKERY, "")],
                         ["seafood", "meat", "fruit", "veg", "dairy", "other", "other"])

    def test_storage_table_entries_that_name_no_kind(self):
        self.assertEqual(kind_table_problems(RULES), [])
        rules = with_rules(fridge_days={"bakery": 5}, unstated_days={"meat": 60})
        self.assertEqual(kind_table_problems(rules), [
            "fridge_days: 'bakery' isn't a section kind (seafood, meat, fruit, veg, dairy, other); ignored"])


class TestExpiry(unittest.TestCase):

    def test_written_dates_are_not_estimates(self):
        found = expiry(VEG, "x", "保鮮至 2026-10-20", date(2026, 10, 1), RULES)
        self.assertEqual((found.on, found.estimated), (date(2026, 10, 20), False))
        self.assertEqual(found.explain(), "2026-10-20, written in the note")

    def test_estimates_count_from_the_purchase_date(self):
        found = expiry(FRUIT, "草莓 (Strawberries)", "冷藏保存", date(2026, 10, 12), RULES)
        self.assertEqual((found.on, found.estimated, found.days_left(date(2026, 10, 16))), (date(2026, 10, 17), True, 1))
        self.assertEqual(found.explain(), "入庫日期 2026-10-12 + 5 days "
                                          "(shelf_life_days: 莓 / berr / 香蕉 / banana / 酪梨 / avocado) = 2026-10-17")

    def test_no_date_without_a_purchase_date(self):
        found = expiry(FRUIT, "草莓 (Strawberries)", "冷藏保存", None, RULES)
        self.assertEqual((found.on, found.estimated, found.days_left(date(2026, 10, 16))), (None, False, None))
        self.assertEqual(found.explain(), "no expiry: no 入庫日期 and no date in the note")

    def test_with_expiry(self):
        self.assertEqual(with_expiry("冷藏保存", date(2026, 10, 23)), "冷藏保存，保鮮至 2026-10-23")
        self.assertEqual(with_expiry("冷藏保存，保鮮至 2026-10-23", date(2026, 10, 30)), "冷藏保存，保鮮至 2026-10-30")
        self.assertEqual(with_expiry("", date(2026, 10, 23)), "保鮮至 2026-10-23")


if __name__ == "__main__":
    unittest.main()
