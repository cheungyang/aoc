import json
import os
import unittest
from unittest.mock import patch

from core.loaders.tools_loader import ToolsLoader
from tests.helpers import envelope, execution_context
from tests.agents.meal_planner import INVENTORY, MEAL_LOGS, KitchenFilesCase
from agents.meal_planner.tools import kitchen_inventory as module
from agents.meal_planner.tools.kitchen_inventory import READ_ACTIONS, kitchen_inventory


def tool_level_only(loader, ctx, tool_id, action_name=None, path=None, **kwargs):
    """Grants the tool itself and refuses every action on a file."""
    return action_name is None


class TestKitchenInventory(KitchenFilesCase):
    tool_module = "agents.meal_planner.tools.kitchen_inventory"

    def call(self, **args):
        with execution_context(agent_id="meal-planner"):
            return envelope(kitchen_inventory.invoke(args))

    def test_the_check_in_is_one_call(self):
        reply = self.call(
            op="use",
            items=[{"name": "牛肉 (Beef)"}, {"name": "Celery", "portions": 0.5}],
            meal={"dishes": "芹菜炒牛肉"},
            ideas=[{"theme": "清爽海味", "dishes": ["香煎鮭魚", "蒜炒小黃瓜"]}],
        )
        self.assertEqual((reply["status"], reply["issues"], reply["errors"]), ("ok", "None", "None"))
        self.assertEqual(json.loads(reply["payload"]), {
            "changed": [
                {"item": "牛肉 (Beef)", "used": "1 份", "removed": True},
                {"item": "西洋芹 / 芹菜 (Celery)", "used": "0.5 份", "left": "1.5 把 (1.5 份)"},
            ],
            "logged": ["2026-10-16 晚餐", "2026-10-16 晚餐提案 ×1"],
        })
        self.assertEqual(self.read("INVENTORY.md"), INVENTORY
                         .replace("| 牛肉 (Beef) | 1 份 | 2026-09-20 | 冷藏/冷凍保存 |\n", "")
                         .replace("| 西洋芹 / 芹菜 (Celery) | 2 把 (2 份) |", "| 西洋芹 / 芹菜 (Celery) | 1.5 把 (1.5 份) |"))
        self.assertEqual(self.read("MEAL_LOGS.md"), MEAL_LOGS + (
            "\n### 2026-10-16\n- **晚餐**：芹菜炒牛肉\n"
            "- **消耗紀錄**：牛肉（消耗 1 份，完食扣除）、西洋芹 / 芹菜（消耗 0.5 份，剩餘 1.5 把 (1.5 份)）\n"
            "\n### 2026-10-16 晚餐提案 (Dinner Recommendations)\n- **提案一（清爽海味）**：香煎鮭魚 ＋ 蒜炒小黃瓜\n"))

    def test_a_purchase_gets_its_purchase_date_and_no_guessed_expiry(self):
        # 入庫日期 is the purchase date; 保鮮至 only when known, else expiry counts from 入庫日期.
        reply = self.call(op="add", items=[
            {"name": "藍莓 (Blueberries)", "qty": "1 盒", "section": "Fruits", "note": "冷藏保存"}])
        self.assertEqual(json.loads(reply["payload"])["changed"][0],
                         {"item": "藍莓 (Blueberries)", "qty": "1 盒", "section": "水果 (Fruits)", "days_left": 5, "est": True})
        self.assertIn("| 藍莓 (Blueberries) | 1 盒 | 2026-10-16 | 冷藏保存 |\n", self.read("INVENTORY.md"))

    def test_a_known_expiry_is_written(self):
        self.call(op="add", items=[
            {"name": "牛奶 (Milk)", "qty": "1 瓶", "section": "Dairy", "added": "2026-10-14", "expires": "2026-10-25"},
            {"name": "藍莓 (Blueberries)", "qty": "1 盒", "section": "Fruits", "shelf_life_days": 7, "note": "冷藏保存"}])
        inventory = self.read("INVENTORY.md")
        self.assertIn("| 牛奶 (Milk) | 1 瓶 | 2026-10-14 | 保鮮至 2026-10-25 |\n", inventory)
        self.assertIn("| 藍莓 (Blueberries) | 1 盒 | 2026-10-16 | 冷藏保存，保鮮至 2026-10-23 |\n", inventory)

    def test_eating_out_logs_the_meal_and_leaves_the_inventory(self):
        reply = self.call(op="log", meal={"dishes": "外食（拉麵）"})
        self.assertEqual(json.loads(reply["payload"]), {"logged": ["2026-10-16 晚餐"]})
        self.assertEqual(self.read("INVENTORY.md"), INVENTORY)
        self.assertTrue(self.read("MEAL_LOGS.md").endswith("\n### 2026-10-16\n- **晚餐**：外食（拉麵）\n"))

    def test_yesterdays_lunch(self):
        self.call(op="log", meal={"dishes": "水餃", "label": "午餐", "days_ago": 1})
        self.assertTrue(self.read("MEAL_LOGS.md").endswith("\n### 2026-10-15\n- **午餐**：水餃\n"))

    def test_items_that_cannot_apply_are_reported_and_the_rest_apply(self):
        reply = self.call(op="use", items=[{"name": "青花菜 (Broccoli)"}, {"name": "牛肉 (Beef)"}])
        self.assertEqual((reply["status"], reply["issues"]), ("partial", "- 青花菜 (Broccoli): not in the inventory"))
        self.assertEqual(json.loads(reply["payload"])["changed"], [{"item": "牛肉 (Beef)", "used": "1 份", "removed": True}])
        self.assertNotIn("牛肉 (Beef)", self.read("INVENTORY.md"))

    def test_an_item_on_a_line_the_parser_cannot_read_comes_back_raw(self):
        self.write("INVENTORY.md", INVENTORY + "- 青花菜 (Broccoli) 1 份\n")
        reply = self.call(op="use", items=[{"name": "青花菜 (Broccoli)"}])
        self.assertEqual(reply["status"], "partial")
        self.assertIn("- 青花菜 (Broccoli): not in the inventory", reply["issues"])
        self.assertIn("- pkm/wallet/kitchen/INVENTORY.md: 1 line not recognized\n    - 青花菜 (Broccoli) 1 份", reply["issues"])

    def test_nothing_changed_means_nothing_written(self):
        self.call(op="use", items=[{"name": "青花菜 (Broccoli)"}])
        self.assertEqual(self.read("INVENTORY.md"), INVENTORY)

    def test_a_missing_file_fails_the_call_and_writes_nothing(self):
        os.remove(self.path("MEAL_LOGS.md"))
        reply = self.call(op="use", items=[{"name": "牛肉 (Beef)"}], meal={"dishes": "牛肉麵"})
        self.assertEqual(reply["status"], "failed")
        self.assertEqual(reply["issues"], "- pkm/wallet/kitchen/MEAL_LOGS.md: file not found (required)")
        self.assertEqual(self.read("INVENTORY.md"), INVENTORY)
        self.assertFalse(os.path.exists(self.path("MEAL_LOGS.md")))

    def test_a_missing_inventory_is_not_recreated(self):
        os.remove(self.path("INVENTORY.md"))
        reply = self.call(op="add", items=[{"name": "藍莓 (Blueberries)", "section": "Fruits"}])
        self.assertEqual(reply["status"], "failed")
        self.assertIn("INVENTORY.md: file not found (required)", reply["issues"])
        self.assertFalse(os.path.exists(self.path("INVENTORY.md")))

    def test_a_refused_permission_writes_nothing(self):
        with patch.object(ToolsLoader, "check_permission", tool_level_only):
            reply = self.call(op="use", items=[{"name": "牛肉 (Beef)"}], meal={"dishes": "牛肉麵"})
        self.assertEqual(reply["status"], "failed")
        self.assertIn("does not have permission to 'use' pkm/wallet/kitchen/INVENTORY.md", reply["errors"])
        self.assertIn("'log' pkm/wallet/kitchen/MEAL_LOGS.md", reply["errors"])
        self.assertEqual((self.read("INVENTORY.md"), self.read("MEAL_LOGS.md")), (INVENTORY, MEAL_LOGS))

    def test_every_write_is_checked_under_its_action_and_read_only_runs_refuse_them(self):
        self.assertEqual(tuple(READ_ACTIONS), ())
        checked = []

        def read_only(loader, ctx, tool_id, action_name=None, path=None, **kwargs):
            checked.append((tool_id, action_name))
            return action_name is None or action_name in READ_ACTIONS  # as scripts/voice_dryrun.py's guard

        with patch.object(ToolsLoader, "check_permission", read_only):
            reply = self.call(op="add", items=[{"name": "藍莓 (Blueberries)", "section": "Fruits"}],
                              ideas=[{"theme": "", "dishes": ["蔥爆豬肉"]}])
        self.assertEqual(reply["status"], "failed")
        self.assertEqual(checked, [("kitchen_inventory", None), ("kitchen_inventory", "add"), ("kitchen_inventory", "log")])
        self.assertEqual((self.read("INVENTORY.md"), self.read("MEAL_LOGS.md")), (INVENTORY, MEAL_LOGS))

    def test_meal_planner_reads_the_kitchen_and_writes_its_files_only_through_the_tool(self):
        with execution_context(agent_id="meal-planner") as ctx:
            loader = ToolsLoader()
            self.assertTrue(loader.check_permission(ctx, "kitchen_inventory", "use", self.path("INVENTORY.md")))
            self.assertTrue(loader.check_permission(ctx, "kitchen_snapshot"))
            # Grants are repo-relative paths; only the path is resolved, nothing is opened.
            for action in ("read", "ls", "grep"):
                self.assertTrue(loader.check_permission(ctx, "filesystem", action, "pkm/wallet/kitchen/INVENTORY.md"))
                self.assertTrue(loader.check_permission(ctx, "filesystem", action, "pkm/wallet/kitchen/MEAL_LOGS.md"))
            self.assertFalse(loader.check_permission(ctx, "filesystem", "overwrite", "pkm/wallet/kitchen/INVENTORY.md"))
            self.assertFalse(loader.check_permission(ctx, "filesystem", "replace_block", "pkm/wallet/kitchen/INVENTORY.md"))
            self.assertFalse(loader.check_permission(ctx, "filesystem", "append", "pkm/wallet/kitchen/MEAL_LOGS.md"))
            self.assertTrue(loader.check_permission(ctx, "filesystem", "replace_block", "pkm/wallet/kitchen/STAPLES.md"))

    def test_rules_md_values_apply(self):
        self.write_rules("- default_portions: 1\n- shelf_life_days:\n  - 藍莓: 10\n")
        reply = self.call(op="add", items=[{"name": "藍莓 (Blueberries)", "section": "Fruits", "note": "冷藏保存"}])
        self.assertEqual(reply["status"], "ok")
        self.assertEqual(json.loads(reply["payload"])["changed"][0],
                         {"item": "藍莓 (Blueberries)", "qty": "1 份", "section": "水果 (Fruits)", "days_left": 10, "est": True})

    def test_an_unreadable_rules_line_is_an_issue_and_the_default_applies(self):
        self.write_rules("- default_portions: lots\n")
        reply = self.call(op="add", items=[{"name": "藍莓 (Blueberries)", "section": "Fruits"}])
        self.assertEqual(reply["status"], "partial")
        self.assertIn("pkm/agents/meal-planner/RULES.md: line 1: 'default_portions: lots' can't be read", reply["issues"])
        self.assertEqual(json.loads(reply["payload"])["changed"][0]["qty"], "2 份")

    def test_explain_shows_the_arithmetic(self):
        reply = self.call(op="use", items=[{"name": "豬排骨 (Pork ribs)", "portions": 0.5}], explain=True)
        self.assertIn("- pkm/wallet/kitchen/INVENTORY.md (modified ", reply["explain"])
        self.assertIn("- 豬排骨 (Pork ribs): 1 盒 (2 份) - 0.5 份 = 0.75 盒 (1.5 份)", reply["explain"])
        reply = self.call(op="add", items=[{"name": "雞腿 (Chicken thighs)", "section": "肉類", "note": "冷藏保存"}],
                          explain=True)
        self.assertIn("雞腿 (Chicken thighs): 入庫日期 2026-10-16 + 3 days (fridge_days: meat) = 2026-10-19; days_left 3",
                      reply["explain"])

    def test_requests_that_cannot_run(self):
        for args, message in (({"op": "use"}, "needs items"),
                              ({"op": "log", "ideas": [{"theme": "x", "dishes": [" "]}]}, "needs a meal or ideas"),
                              ({"op": "log", "meal": {"dishes": " "}}, "needs its dishes")):
            reply = self.call(**args)
            self.assertEqual(reply["status"], "failed")
            self.assertIn(message, reply["errors"])
        self.assertEqual((self.read("INVENTORY.md"), self.read("MEAL_LOGS.md")), (INVENTORY, MEAL_LOGS))

    def test_an_unexpected_failure_is_reported_not_raised(self):
        with patch.object(module, "write_atomic", side_effect=OSError("disk full")):
            reply = self.call(op="use", items=[{"name": "牛肉 (Beef)"}])
        self.assertEqual(reply["status"], "failed")
        self.assertIn("OSError: disk full", reply["errors"])

    def test_outside_an_agent_run(self):
        self.assertIn("no active execution context", kitchen_inventory.invoke({"op": "log", "meal": {"dishes": "x"}}))


if __name__ == "__main__":
    unittest.main()
