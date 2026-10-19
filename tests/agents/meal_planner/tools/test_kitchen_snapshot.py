import json
import os
import unittest
from unittest.mock import patch

from core.loaders.tools_loader import ToolsLoader
from tests.helpers import envelope, execution_context
from tests.agents.meal_planner import INVENTORY, MEAL_LOGS, STAPLES, KitchenFilesCase
from agents.meal_planner.tools.kitchen_snapshot import kitchen_snapshot


class TestKitchenSnapshot(KitchenFilesCase):
    tool_module = "agents.meal_planner.tools.kitchen_snapshot"

    def call(self, **args):
        with execution_context(agent_id="meal-planner"):
            return envelope(kitchen_snapshot.invoke(args))

    def test_one_read_returns_the_planning_view(self):
        reply = self.call()
        self.assertEqual((reply["status"], reply["issues"], reply["errors"]), ("ok", "None", "None"))
        snapshot = json.loads(reply["payload"])
        self.assertEqual(snapshot["today"], "2026-10-16")
        self.assertEqual(snapshot["expiring_soon"][0], {"item": "草莓 (Strawberries)", "qty": "1 盒", "days_left": 2, "est": True})
        self.assertIn("牛肉 (Beef) 1 份", snapshot["long_life"]["肉類 (Meats)"])
        self.assertEqual(snapshot["staples"], ["醬油 (Soy Sauce)", "米 (Rice)", "食用油 (Cooking Oil)"])
        self.assertEqual(snapshot["recent_meals"][0]["days_ago"], 3)
        self.assertEqual(snapshot["last_ideas"]["date"], "2026-10-12")
        self.assertNotIn("explain", reply)

    def test_it_only_reads(self):
        self.call()
        self.assertEqual((self.read("INVENTORY.md"), self.read("MEAL_LOGS.md")), (INVENTORY, MEAL_LOGS))

    def test_a_missing_inventory_fails_instead_of_showing_an_empty_kitchen(self):
        os.remove(self.path("INVENTORY.md"))
        reply = self.call()
        self.assertEqual(reply["status"], "failed")
        self.assertEqual(reply["issues"], "- pkm/wallet/kitchen/INVENTORY.md: file not found (required)")
        self.assertEqual(reply["payload"], "")

    def test_missing_staples_and_meal_logs_are_partial(self):
        for name in ("MEAL_LOGS.md", "STAPLES.md"):
            os.remove(self.path(name))
        reply = self.call()
        self.assertEqual(reply["status"], "partial")
        self.assertEqual(reply["issues"], "- pkm/wallet/kitchen/MEAL_LOGS.md: file not found\n"
                                          "- pkm/wallet/kitchen/STAPLES.md: file not found")
        snapshot = json.loads(reply["payload"])
        self.assertEqual((snapshot["staples"], snapshot["recent_meals"]), ([], []))
        self.assertEqual(snapshot["expiring_soon"][0]["item"], "草莓 (Strawberries)")

    def test_lines_it_cannot_read_come_back_raw(self):
        self.write("INVENTORY.md", INVENTORY + "- 青花菜 (Broccoli) 1 份\n")
        self.write("MEAL_LOGS.md", MEAL_LOGS + "\n### 2026-10-15\n晚餐：牛肉麵\n")
        self.write("STAPLES.md", STAPLES + "\n鹽 (Salt)\n")
        reply = self.call()
        self.assertEqual(reply["status"], "partial")
        self.assertEqual(reply["issues"], (
            "- pkm/wallet/kitchen/INVENTORY.md: 1 line not recognized\n    - 青花菜 (Broccoli) 1 份\n"
            "- pkm/wallet/kitchen/MEAL_LOGS.md: 1 line not recognized\n    晚餐：牛肉麵\n"
            "- pkm/wallet/kitchen/STAPLES.md: 1 line not recognized\n    鹽 (Salt)"))

    def test_a_row_without_any_date_comes_back_raw_not_guessed(self):
        self.write("INVENTORY.md", INVENTORY.replace("| 牛肉 (Beef) | 1 份 | 2026-09-20 |", "| 牛肉 (Beef) | 1 份 | 9/20 |"))
        reply = self.call()
        self.assertEqual(reply["status"], "partial")
        self.assertEqual(reply["issues"], (
            "- pkm/wallet/kitchen/INVENTORY.md: 1 row has no 入庫日期 and no date in the note, "
            "so how fresh they are is unknown\n    | 牛肉 (Beef) | 1 份 | 9/20 | 冷藏/冷凍保存 |"))
        self.assertNotIn("牛肉 (Beef) 1 份", json.loads(reply["payload"])["long_life"]["肉類 (Meats)"])

    def test_rules_md_changes_the_estimate(self):
        self.write_rules("- expiring_soon_days: 5\n- shelf_life_days:  # strawberries keep less\n  - 草莓: 2\n")
        reply = self.call()
        self.assertEqual(reply["status"], "ok")
        snapshot = json.loads(reply["payload"])
        # Bought 2026-10-13: 2 days is 2026-10-15, one day past; tofu (4 days left) is now expiring soon.
        self.assertIn({"item": "草莓 (Strawberries)", "qty": "1 盒", "days_left": -1, "est": True}, snapshot["expired"])
        self.assertIn("嫩豆腐 (Silken tofu)", [e["item"] for e in snapshot["expiring_soon"]])

    def test_a_storage_table_entry_for_no_section_kind_is_an_issue(self):
        self.write_rules("- fridge_days:\n  - bakery: 5\n")
        reply = self.call()
        self.assertEqual(reply["status"], "partial")
        self.assertIn("- pkm/agents/meal-planner/RULES.md: fridge_days: 'bakery' isn't a section kind", reply["issues"])

    def test_explain_lists_the_files_and_how_each_number_was_reached(self):
        explain = self.call(explain=True)["explain"]
        self.assertIn("- pkm/wallet/kitchen/INVENTORY.md (modified ", explain)
        self.assertIn(", 16 records)", explain)
        self.assertIn("- pkm/wallet/kitchen/STAPLES.md (modified ", explain)
        self.assertIn("- lists: expired below 0 days left, expiring_soon up to 3, perishables up to 30, long_life beyond; "
                      "meals of the last 14 days", explain)
        self.assertIn("- 草莓 (Strawberries): 入庫日期 2026-10-13 + 5 days "
                      "(shelf_life_days: 莓 / berr / 香蕉 / banana / 酪梨 / avocado) = 2026-10-18; days_left 2", explain)

    def test_an_agent_without_the_grant_is_refused(self):
        with patch.object(ToolsLoader, "check_permission", return_value=False):
            reply = self.call()
        self.assertEqual(reply["status"], "failed")
        self.assertIn("does not have permission", reply["errors"])

    def test_outside_an_agent_run(self):
        self.assertIn("no active execution context", kitchen_snapshot.invoke({}))


if __name__ == "__main__":
    unittest.main()
