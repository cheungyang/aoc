"""Tests for agents/meal-planner, one module per module.

The fixtures are made-up kitchen files in the format of pkm/wallet/kitchen,
quirks kept: a row with no space before a '|', the '冷冷凍' typo in a heading,
a date heading that appears twice, a date range, and ideas with indented
sub-bullets.

`KitchenFilesCase` puts copies of them in a temp PKM, for the tools and the scan.
"""
import os
import tempfile
import unittest
from datetime import date, datetime
from unittest.mock import patch

from core.util.config import Config
from core.util.time_util import get_local_timezone

TODAY = date(2026, 10, 16)

INVENTORY = """# 廚房庫存 (Kitchen Inventory)
> 註：1 份 = 一餐的份量。

### 肉類 (Meats)
| 食材名稱 | 剩餘份量 / 數量 (以份計) | 入庫日期 | 預計保鮮期 / 備註 |
| :--- | :--- | :--- | :--- |
| 牛肉 (Beef) | 1 份 | 2026-09-20 | 冷藏/冷凍保存 |
| 羊肉 (Lamb) | 0.5 份 | 2026-09-20 | 冷藏/冷凍保存 |
| 豬排骨 (Pork ribs) | 1 盒 (2 份) | 2026-10-11 | 冷藏/冷凍保存，燉湯用 |

### 蔬菜與鮮蔬 (Fresh Vegetables)
| 食材名稱 | 剩餘份量 / 數量 (以份計) | 入庫日期 | 預計保鮮期 / 備註 |
| :--- | :--- | :--- | :--- |
| 西洋芹 / 芹菜 (Celery) | 2 把 (2 份) | 2026-10-05 | 冷藏保存 |
| 金針菇 (Enoki mushroom) | 1 包 | 2026-09-26 | 冷藏保存 |
| 黃洋蔥 (Brown onion) | 1 袋 | 2026-09-26 | 陰涼常溫保存 |
| 小黃瓜 (Cucumbers) | 3 條 | 2026-10-09 | 冷藏保存，約 10 天 |

### 冷冷凍麵點、烘焙與豆製品 (Frozen Staples, Bakery & Tofu/Soy)
| 食材名稱 | 剩餘份量 / 數量 (以份計) | 入庫日期 | 預計保鮮期 / 備註 |
| :--- | :--- | :--- | :--- |
| 嫩豆腐 (Silken tofu) | 1 盒 | 2026-10-13 | 冷藏保存 |
| 蔥油餅 (Scallion pancakes)| 1 包 | 2026-09-26 | 冷凍保存 |

### 蛋品、豆漿與乳製品 (Eggs, Soy Milk & Dairy)
| 食材名稱 | 剩餘份量 / 數量 | 入庫日期 | 預計保鮮期 / 備註 |
| :--- | :--- | :--- | :--- |
| 放牧雞蛋 (Eggs) | 24 顆 (2 打) | 2026-10-13 | 冷藏保存 |
| 有機全脂鮮乳 (Whole milk) | 3 瓶 | 2026-10-13 | 冷藏保存 |
| 全脂鮮乳 (Whole milk) | 1 瓶 | 2026-09-26 | 冷藏保存 |

### 醃漬小菜與醬料 (Pickles & Sauces)
| 食材名稱 | 剩餘份量 / 數量 | 入庫日期 | 預計保鮮期 / 備註 |
| :--- | :--- | :--- | :--- |
| 蠔油 (Oyster sauce) | 1 瓶 | 2026-09-20 | 開封後冷藏 |
| 豆腐乳 (Fermented bean curd) | 1 罐 | 2026-09-20 | 常溫/冷藏保存 |

### 水果 (Fruits)
| 食材名稱 | 剩餘份量 / 數量 | 入庫日期 | 預計保鮮期 / 備註 |
| :--- | :--- | :--- | :--- |
| 草莓 (Strawberries) | 1 盒 | 2026-10-13 | 冷藏保存 |
| 鳳梨 (Pineapple) | 1 顆 | 2026-10-11 | 常溫/冷藏 |
"""

MEAL_LOGS = """# 膳食日誌 (Meal Logs)

### 2026-09-27 ~ 2026-09-28
- **餐點**：剩菜與水餃（無庫存消耗）

### 2026-10-01
- **晚餐**：咖哩雞飯 (Chicken curry rice)（扣減雞腿 1 份）

### 2026-10-03
- **晚餐**：蒜香烤雞腿 (Garlic roast chicken thighs)、清炒高麗菜（扣減雞腿 1 份）

### 2026-10-05
- **晚餐**：番茄炒蛋、白飯


### 2026-10-05
- **晚餐**：番茄炒蛋、白飯
- **點心**：香蕉

### 2026-10-11
- **晚餐**：香菇雞湯麵（Shiitake chicken noodle soup）
- **消耗紀錄**：香菇（全完食扣除）、雞腿（消耗 1 份，剩餘 1 份）

### 2026-10-12 晚餐提案 (Dinner Recommendations)
- **提案一（清爽家常）**：清蒸鱈魚 ＋ 蒜炒菠菜 ＋ 味噌豆腐湯
  - *主要食材*：鱈魚、菠菜、嫩豆腐、味噌、蒜末
- **提案二（快炒與湯）**：青椒炒牛肉 ＋ 涼拌小黃瓜 ＋ 玉米濃湯

### 2026-10-13
- **晚餐**：雞肉蔬菜湯麵（Chicken & vegetable noodle soup）
- **消耗紀錄**：雞胸肉（全完食扣除）、紅蘿蔔（消耗 1 根）
"""

STAPLES = """# 廚房常備品 (Kitchen Staples)
- 醬油 (Soy Sauce)
- 米 (Rice)
- 食用油 (Cooking Oil)"""


class KitchenFilesCase(unittest.TestCase):
    """Config().pkm_dir points at a temp dir holding the fixture files, and the tool
    module's clock reads TODAY at 21:05. The real pkm is never touched."""

    tool_module = ""  # e.g. "agents.meal_planner.tools.kitchen_inventory"; empty: no clock patch

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        real_pkm = os.path.realpath(Config().pkm_dir)
        Config().pkm_dir = tmp.name
        self.addCleanup(setattr, Config(), "pkm_dir", None)
        self.pkm = tmp.name
        self.kitchen = os.path.join(tmp.name, "wallet", "kitchen")
        self.assertFalse(os.path.realpath(self.kitchen).startswith(real_pkm + os.sep))
        os.makedirs(self.kitchen)
        for name, text in (("INVENTORY.md", INVENTORY), ("MEAL_LOGS.md", MEAL_LOGS), ("STAPLES.md", STAPLES)):
            self.write(name, text)
        if self.tool_module:
            now = datetime(TODAY.year, TODAY.month, TODAY.day, 21, 5, tzinfo=get_local_timezone())
            clock = patch(f"{self.tool_module}.get_local_now", return_value=now)
            clock.start()
            self.addCleanup(clock.stop)

    def path(self, name: str) -> str:
        return os.path.join(self.kitchen, name)

    def read(self, name: str) -> str:
        with open(self.path(name), encoding="utf-8", newline="") as f:
            return f.read()

    def write(self, name: str, text: str) -> None:
        with open(self.path(name), "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def write_rules(self, text: str) -> None:
        """meal-planner's RULES.md in the temp PKM."""
        folder = os.path.join(self.pkm, "agents", "meal-planner")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "RULES.md"), "w", encoding="utf-8") as f:
            f.write(text)
