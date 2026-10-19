import unittest
from datetime import date

from tests.agents.meal_planner import MEAL_LOGS, TODAY
from agents.meal_planner.lib.meal_log import (
    MealIdea, _chinese_number, last_ideas, log_ideas, log_meal, logged_meal, recent_meals, unrecognized,
)


class TestReading(unittest.TestCase):

    def test_recent_meals_newest_first_with_days_ago(self):
        meals = recent_meals(MEAL_LOGS, TODAY)
        self.assertEqual([m["date"] for m in meals], ["2026-10-13", "2026-10-11", "2026-10-05", "2026-10-03"])
        self.assertEqual(meals[0], {"date": "2026-10-13", "days_ago": 3, "meals": [
            "晚餐：雞肉蔬菜湯麵（Chicken & vegetable noodle soup）"]})

    def test_stock_records_snacks_and_ideas_are_not_meals(self):
        text = " ".join(m for day in recent_meals(MEAL_LOGS, TODAY) for m in day["meals"])
        for word in ("消耗紀錄", "香蕉", "提案", "清蒸鱈魚"):
            self.assertNotIn(word, text)

    def test_a_meal_line_loses_its_stock_note(self):
        day = next(m for m in recent_meals(MEAL_LOGS, TODAY) if m["date"] == "2026-10-03")
        self.assertEqual(day["meals"], ["晚餐：蒜香烤雞腿 (Garlic roast chicken thighs)、清炒高麗菜"])

    def test_a_date_heading_that_appears_twice_is_one_day(self):
        day = next(m for m in recent_meals(MEAL_LOGS, TODAY) if m["date"] == "2026-10-05")
        self.assertEqual(day["meals"], ["晚餐：番茄炒蛋、白飯"])

    def test_the_window_is_the_last_14_days(self):
        dates = [m["date"] for m in recent_meals(MEAL_LOGS, TODAY)]
        self.assertNotIn("2026-10-01", dates)  # 15 days ago
        self.assertIn("2026-10-01", [m["date"] for m in recent_meals(MEAL_LOGS, date(2026, 10, 14))])

    def test_last_ideas(self):
        self.assertEqual(last_ideas(MEAL_LOGS, TODAY), {"date": "2026-10-12", "days_ago": 4, "ideas": [
            "清蒸鱈魚 ＋ 蒜炒菠菜 ＋ 味噌豆腐湯", "青椒炒牛肉 ＋ 涼拌小黃瓜 ＋ 玉米濃湯"]})
        self.assertIsNone(last_ideas(MEAL_LOGS, date(2026, 11, 30)))

    def test_logged_meal(self):
        self.assertEqual(logged_meal(MEAL_LOGS, date(2026, 10, 13)),
                         "晚餐：雞肉蔬菜湯麵（Chicken & vegetable noodle soup）")
        self.assertIsNone(logged_meal(MEAL_LOGS, date(2026, 10, 12)))  # only ideas that day
        self.assertIsNone(logged_meal(MEAL_LOGS, TODAY))
        self.assertEqual(logged_meal(MEAL_LOGS, date(2026, 10, 5), "點心"), "點心：香蕉")


class TestUnrecognized(unittest.TestCase):

    def test_the_fixture_reads_cleanly(self):
        self.assertEqual(unrecognized(MEAL_LOGS, TODAY, 14), [])
        self.assertEqual(unrecognized(MEAL_LOGS, TODAY, 100), [])

    def test_a_line_that_is_not_an_entry_in_the_window(self):
        text = MEAL_LOGS + "\n### 2026-10-15\n晚餐：牛肉麵\n> 註：剩一碗\n- **午餐**：水餃\n  - 配酸辣湯\n"
        self.assertEqual(unrecognized(text, TODAY, 14), ["晚餐：牛肉麵"])

    def test_older_days_are_not_read_so_not_listed(self):
        text = MEAL_LOGS.replace("### 2026-10-01\n", "### 2026-10-01\n晚餐：牛肉麵\n")
        self.assertEqual(unrecognized(text, TODAY, 14), [])
        self.assertEqual(unrecognized(text, TODAY, 16), ["晚餐：牛肉麵"])

    def test_lines_whose_day_cannot_be_told(self):
        text = MEAL_LOGS + "\n### 下週計畫\n- **晚餐**：咖哩\n\n### 2026-02-30\n- **晚餐**：拉麵\n###2026-10-15\n"
        self.assertEqual(unrecognized(text, TODAY, 1), [
            "### 下週計畫", "- **晚餐**：咖哩", "### 2026-02-30", "- **晚餐**：拉麵", "###2026-10-15"])

    def test_a_heading_with_nothing_under_it_is_fine(self):
        self.assertEqual(unrecognized("# 膳食日誌\n\n## 十月\n### 2026-10-16\n- **晚餐**：麵\n", TODAY, 1), [])


class TestLogging(unittest.TestCase):

    def test_a_meal_on_a_new_day_goes_at_the_end(self):
        text = log_meal(MEAL_LOGS, TODAY, "晚餐", "番茄炒蛋、蒜炒青菜", ["羊肉（消耗 0.5 份，完食扣除）"])
        self.assertEqual(text, MEAL_LOGS + "\n### 2026-10-16\n- **晚餐**：番茄炒蛋、蒜炒青菜\n"
                                           "- **消耗紀錄**：羊肉（消耗 0.5 份，完食扣除）\n")

    def test_a_meal_on_a_logged_day_joins_that_section(self):
        text = log_meal(MEAL_LOGS, date(2026, 10, 11), "午餐", "水餃")
        self.assertIn("- **消耗紀錄**：香菇（全完食扣除）、雞腿（消耗 1 份，剩餘 1 份）\n- **午餐**：水餃\n\n### 2026-10-12", text)
        self.assertEqual(text.replace("- **午餐**：水餃\n", ""), MEAL_LOGS)

    def test_a_meal_is_not_filed_under_that_days_ideas(self):
        text = log_meal(MEAL_LOGS, date(2026, 10, 12), "晚餐", "外食（拉麵）")
        self.assertTrue(text.endswith("\n### 2026-10-12\n- **晚餐**：外食（拉麵）\n"))

    def test_ideas_get_their_own_section(self):
        ideas = [MealIdea(theme="清爽海味", dishes=["香煎鮭魚", "蒜炒高麗菜"]), MealIdea(theme="", dishes=["蔥爆豬肉"])]
        text = log_ideas(MEAL_LOGS, TODAY, ideas)
        self.assertTrue(text.endswith(
            "\n### 2026-10-16 晚餐提案 (Dinner Recommendations)\n"
            "- **提案一（清爽海味）**：香煎鮭魚 ＋ 蒜炒高麗菜\n- **提案二**：蔥爆豬肉\n"))

    def test_more_ideas_the_same_day_continue_the_numbering(self):
        text = log_ideas(MEAL_LOGS, date(2026, 10, 12), [MealIdea(theme="快炒", dishes=["蔥爆牛肉"])])
        self.assertIn("- **提案二（快炒與湯）**：青椒炒牛肉 ＋ 涼拌小黃瓜 ＋ 玉米濃湯\n"
                      "- **提案三（快炒）**：蔥爆牛肉\n\n### 2026-10-13", text)

    def test_an_empty_file_gets_a_title(self):
        self.assertEqual(log_meal("", TODAY, "晚餐", "水餃"), "# 膳食日誌 (Meal Logs)\n\n### 2026-10-16\n- **晚餐**：水餃\n")

    def test_crlf_and_a_missing_final_newline(self):
        text = log_meal("# 膳食日誌\r\n\r\n### 2026-10-16\r\n- **午餐**：麵", TODAY, "晚餐", "飯")
        self.assertEqual(text, "# 膳食日誌\r\n\r\n### 2026-10-16\r\n- **午餐**：麵\r\n- **晚餐**：飯\r\n")

    def test_entries_stay_on_one_line(self):
        self.assertTrue(log_meal("", TODAY, "晚餐", "番茄\n炒蛋").endswith("- **晚餐**：番茄 炒蛋\n"))

    def test_chinese_numbers(self):
        self.assertEqual([_chinese_number(n) for n in (1, 3, 10, 12, 20, 23)], ["一", "三", "十", "十二", "二十", "二十三"])


if __name__ == "__main__":
    unittest.main()
