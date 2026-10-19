"""meal-planner's thresholds: what each one means, and its default.

The user's values go in pkm/agents/meal-planner/RULES.md (core/tooling/rules.py).
agents/meal-planner/WORKFLOW.md says where each one applies, with examples.
"""
import copy

from core.tooling import Rule

RULES = (
    # Items that keep differently from the rest of their section, checked in order
    # against the row's name (the first match wins, so 青蔥 is matched before 洋蔥).
    Rule("shelf_life_days", {
        "皮蛋 / century egg": 90,
        "鹹鴨蛋 / 鹹蛋 / salted egg": 30,
        '雞蛋 / 鴨蛋 / "egg" / "eggs"': 35,
        '麵包 / 吐司 / 饅頭 / 可頌 / "bread" / toast / croissant / bagel': 7,
        '鮮奶油 / "cream"': 14,
        "優格 / 優酪 / yogurt / yoghurt": 21,
        "起司 / 乳酪 / 芝士 / cheese": 30,
        '鮮乳 / 鮮奶 / 牛奶 / "milk"': 10,
        "豆腐 / tofu": 7,
        "菠菜 / 生菜 / 萵苣 / 青江 / 空心菜 / 地瓜葉 / 豆苗 / 豆芽 / spinach / lettuce / bok choy / sprout": 4,
        '青蔥 / 蔥花 / "scallion" / "scallions" / "green onion" / "green onions" / "spring onion" / "spring onions"': 7,
        "南瓜 / pumpkin / 洋蔥 / onion / 大蒜 / garlic": 60,
        "馬鈴薯 / potato / 地瓜 / 番薯 / 芋頭 / taro": 45,
        "紅蘿蔔 / 胡蘿蔔 / carrot": 28,
        "薑 / ginger": 21,
        "蘿蔔 / daikon / radish / 芹 / celery / 白菜 / 高麗菜 / cabbage": 14,
        '蘋果 / "apple" / "apples"': 45,
        "柑 / 橘 / 橙 / 柚 / 檸檬 / orange / mandarin / lemon / citrus": 14,
        "莓 / berr / 香蕉 / banana / 酪梨 / avocado": 5,
    }, 'days an item keeps from its 入庫日期, by name: names split by /, a "quoted" name is a whole word; '
       "entries you add are checked first"),
    Rule("sauces_snacks_days", 180, "days anything in a sauce, pickle, drink or snack section keeps"),
    Rule("frozen_days", 90, "days a frozen item keeps (冷凍 or frozen in its name or note)"),
    Rule("dried_days", 180, "days a dried item keeps (乾 or dried in its name or note)"),
    Rule("fridge_days", {"meat": 3, "seafood": 2, "dairy": 10, "fruit": 7, "veg": 7, "other": 14},
         "days an item kept in the fridge (冷藏) keeps, by the kind of its section"),
    Rule("cool_dark_days", 30, "days an item kept cool and dark (陰涼) keeps"),
    Rule("room_temp_days", {"fruit": 7, "veg": 14, "other": 180},
         "days an item kept at room temperature (常溫) keeps, by the kind of its section"),
    Rule("unstated_days", {"meat": 90, "seafood": 90, "dairy": 10, "fruit": 7, "veg": 7, "other": 180},
         "days an item whose note names no storage keeps, by the kind of its section (meat and fish count as frozen)"),
    Rule("expiring_soon_days", 3, "kitchen_snapshot lists a row as expiring soon with this many days left or fewer"),
    Rule("perishable_days", 30, "rows with more days left than this are listed as long life"),
    Rule("recent_meal_days", 14, "days of meals and dinner ideas kitchen_snapshot returns, today included"),
    Rule("default_portions", 2.0, "portions a purchase covers when it gives neither portions nor an amount"),
)


def defaults() -> dict:
    """{key: default}: the values the tools use when RULES.md sets none."""
    return {rule.key: copy.deepcopy(rule.default) for rule in RULES}
