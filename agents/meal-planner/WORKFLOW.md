# meal-planner: how its tools work

The kitchen tools read and write three files in `pkm/wallet/kitchen/`:

- INVENTORY.md: markdown tables under `###` section headings, one row per item: name, amount, 入庫日期 (the purchase date), note.
- MEAL_LOGS.md: dated `###` headings with `- **label**：text` entries; ideas go under `### <date> 晚餐提案 (Dinner Recommendations)`.
- STAPLES.md: a `- item` list of basics that are always stocked.

The numbers they use are in `pkm/agents/meal-planner/RULES.md`. A key RULES.md doesn't set keeps its default, shown here (from `agents/meal-planner/lib/rules.py`). Every example below runs in the test suite, starting from the defaults:

```python
>>> from datetime import date
>>> from agents.meal_planner.lib.rules import defaults
>>> rules = defaults()
```

## kitchen_snapshot

One read of the three files: what to use first, what else is there, and what the family ate lately.

### Expiry

A row's expiry is the first of these it has:

1. A date in its note (`保鮮至 2026-10-23`), or a shelf life in its note (`約 7 天`, `2 週`, `3 個月`, a month being 30 days) counted from its 入庫日期.
2. Its 入庫日期 plus an estimated shelf life (below). The row is marked `"est": true`.
3. Neither: a row with no date in its note and no readable 入庫日期 has no expiry. It comes back raw under issues, never guessed.

Blueberries with a written date, pea shoots that keep "about 5 days", and strawberries bought on Oct 12 with only a storage note:

```python
>>> from agents.meal_planner.lib.shelf_life import estimate, expiry
>>> found = expiry("水果 (Fruits)", "藍莓 (Blueberries)", "冷藏保存，保鮮至 2026-10-23", date(2026, 10, 16), rules)
>>> found.on, found.estimated
(datetime.date(2026, 10, 23), False)
>>> expiry("蔬菜 (Vegetables)", "豆苗 (Pea shoots)", "約 5 天", date(2026, 10, 10), rules).on
datetime.date(2026, 10, 15)
>>> found = expiry("水果 (Fruits)", "草莓 (Strawberries)", "冷藏保存", date(2026, 10, 12), rules)
>>> found.explain()
'入庫日期 2026-10-12 + 5 days (shelf_life_days: 莓 / berr / 香蕉 / banana / 酪梨 / avocado) = 2026-10-17'
>>> found.days_left(date(2026, 10, 16)), found.estimated
(1, True)
>>> expiry("水果 (Fruits)", "草莓 (Strawberries)", "冷藏保存", None, rules).on is None
True
```

### The estimate

The shelf life is the first rule that applies:

1. An entry you added to `shelf_life_days` whose name is in the item's name.
2. A sauce, pickle, drink or snack section (醬, 醃, 飲品, 休閒, 零食, sauce, pickle, condiment, beverage, drink, snack in the heading): `sauces_snacks_days`, default 180.
3. 冷凍, frozen or freez in the name or note, so 冷藏/冷凍 too (bulk meat and fish go in the freezer): `frozen_days`, default 90.
4. 乾 or dried in the name or note: `dried_days`, default 180.
5. The default entries of `shelf_life_days`, top to bottom: 19 items that keep differently from the rest of their section.
6. The storage note, by the kind of the row's section:
   - 陰涼 (cool and dark): `cool_dark_days`, default 30.
   - 冷藏 or fridge: `fridge_days`, default meat 3, seafood 2, dairy 10, fruit 7, veg 7, other 14.
   - 常溫 (room temperature): `room_temp_days`, default fruit 7, veg 14, other 180.
   - No storage word: `unstated_days`, default meat 90, seafood 90 (taken as frozen), dairy 10, fruit 7, veg 7, other 180.

```python
>>> estimate("醃漬小菜與醬料 (Pickles & Sauces)", "韓式泡菜 (Kimchi)", "冷藏保存", rules)
(180, 'sauces_snacks_days')
>>> estimate("肉類 (Meats)", "牛肉 (Beef)", "冷藏/冷凍保存", rules)
(90, 'frozen_days')
>>> estimate("辛香料、油脂與乾貨 (Aromatics, Fats & Dry Goods)", "乾香菇 (Dried shiitake)", "常溫乾燥保存", rules)
(180, 'dried_days')
>>> estimate("蔬菜與鮮蔬 (Fresh Vegetables)", "黃洋蔥 (Brown onion)", "陰涼常溫保存", rules)
(60, 'shelf_life_days: 南瓜 / pumpkin / 洋蔥 / onion / 大蒜 / garlic')
>>> estimate("雜項 (Misc)", "麵線 (Wheat vermicelli)", "陰涼保存", rules)
(30, 'cool_dark_days')
>>> estimate("肉類 (Meats)", "雞腿 (Chicken thighs)", "冷藏保存", rules)
(3, 'fridge_days: meat')
>>> estimate("水果 (Fruits)", "芒果 (Mango)", "常溫保存", rules)
(7, 'room_temp_days: fruit')
>>> estimate("肉類 (Meats)", "牛排 (Steak)", "", rules)
(90, 'unstated_days: meat')
```

A section's kind comes from its heading: seafood (海鮮, 魚, seafood, fish), meat (肉, meat), fruit (水果, fruit), veg (蔬, vegetable), dairy (乳, 蛋, dairy, egg, milk), else other. The first kind that matches wins. An entry in a storage table for any other kind never applies, and the tools report it.

```python
>>> from agents.meal_planner.lib.shelf_life import section_kind
>>> [section_kind(s) for s in ("海鮮 (Seafood)", "蛋品、豆漿與乳製品 (Eggs, Soy Milk & Dairy)", "冷冷凍麵點、烘焙與豆製品 (Frozen Staples, Bakery & Tofu/Soy)")]
['seafood', 'dairy', 'other']
```

A `shelf_life_days` entry is names split by `/`. A name matches anywhere in the item's name, case-blind, even inside a word: berr is in Strawberries. A name in quotes matches only a whole word: "apple" isn't in Pineapple. Parentheses are plain text, so an entry can be a row's full name, such as `草莓 (Strawberries)`.

```python
>>> estimate("水果 (Fruits)", "鳳梨 (Pineapple)", "常溫/冷藏", rules)
(7, 'fridge_days: fruit')
>>> estimate("水果 (Fruits)", "青蘋果 (Green apples)", "常溫/冷藏", rules)
(45, 'shelf_life_days: 蘋果 / "apple" / "apples"')
```

An entry you add to RULES.md is checked before everything else, so it can be narrower than a default: "strawberries keep 3 days" is one line, and other berries keep 5.

```markdown
- shelf_life_days:
  - 草莓: 3
```

```python
>>> mine = defaults()
>>> mine["shelf_life_days"]["草莓"] = 3
>>> estimate("水果 (Fruits)", "草莓 (Strawberries)", "冷藏保存", mine)
(3, 'shelf_life_days: 草莓')
>>> estimate("水果 (Fruits)", "藍莓 (Blueberries)", "冷藏保存", mine)
(5, 'shelf_life_days: 莓 / berr / 香蕉 / banana / 酪梨 / avocado')
```

Changing a default entry's days keeps its place in the order.

### The lists

Each dated row goes in one list by its days left: below 0 is `expired`; 0 up to `expiring_soon_days` (default 3) is `expiring_soon`; up to `perishable_days` (default 30) is `perishables`; more is `long_life`, listed by section as "name amount". The first three hold `{item, qty, days_left}` plus `"est": true` for an estimate, soonest first; ties keep file order.

```python
>>> from agents.meal_planner.lib.kitchen_view import bucket
>>> [bucket(days, rules) for days in (-2, 0, 3, 4, 30, 31)]
['expired', 'expiring_soon', 'expiring_soon', 'perishables', 'perishables', 'long_life']
```

### Meals and ideas

`recent_meals` covers the last `recent_meal_days` days (default 14), today included, newest first. A meal is an entry whose label has 餐, 料理 or 外食, so 消耗紀錄 lines aren't meals. A trailing stock note in parentheses (剩餘, 扣減, 消耗, ...) is dropped, a line is cut at 80 characters, and a date heading that appears twice is one day. `last_ideas` is the latest 晚餐提案 section in the same window.

```python
>>> from agents.meal_planner.lib.meal_log import last_ideas, recent_meals
>>> log = "\n".join([
...     "### 2026-10-01", "- **晚餐**：咖哩雞飯（扣減雞腿 1 份）",
...     "### 2026-10-12 晚餐提案 (Dinner Recommendations)", "- **提案一（清爽）**：清蒸鱈魚 ＋ 蒜炒青菜",
...     "### 2026-10-13", "- **晚餐**：雞湯麵（剩餘 1 份）", "- **消耗紀錄**：紅蘿蔔（消耗 1 根）",
... ])
>>> recent_meals(log, date(2026, 10, 16), rules["recent_meal_days"])
[{'date': '2026-10-13', 'days_ago': 3, 'meals': ['晚餐：雞湯麵']}]
>>> last_ideas(log, date(2026, 10, 16), rules["recent_meal_days"])
{'date': '2026-10-12', 'days_ago': 4, 'ideas': ['清蒸鱈魚 ＋ 蒜炒青菜']}
```

### What it couldn't read

- INVENTORY.md missing: `failed`. STAPLES.md or MEAL_LOGS.md missing: `partial`, and the rest still comes back.
- These come back raw under issues (`partial`): INVENTORY.md lines that are neither rows nor the file's structure (headings, table headers, notes `> ...`, rules `---`, blank lines); rows with no date at all; MEAL_LOGS.md lines in the days it reads that aren't entries, details indented under one, notes or blank, plus lines whose day can't be told; STAPLES.md lines that aren't items, headings or notes.
- A RULES.md line it can't read, or an unknown key, is an issue too, and the default applies.

```python
>>> from agents.meal_planner.lib.inventory_doc import InventoryDoc
>>> from agents.meal_planner.lib.kitchen_view import build_snapshot
>>> from agents.meal_planner.lib.meal_log import unrecognized
>>> doc = InventoryDoc("\n".join([
...     "# 廚房庫存", "> 註：1 份 = 一餐", "", "### 肉類 (Meats)",
...     "| 食材名稱 | 剩餘份量 / 數量 | 入庫日期 | 預計保鮮期 / 備註 |", "| :--- | :--- | :--- | :--- |",
...     "| 牛肉 (Beef) | 1 份 | 2026-09-20 | 冷藏/冷凍保存 |", "| 羊肉 (Lamb) | 0.5 份 | 9/20 | 冷藏/冷凍保存 |",
...     "", "- 雞腿 (Chicken thighs) 2 份",
... ]))
>>> doc.unrecognized()
['- 雞腿 (Chicken thighs) 2 份']
>>> build_snapshot(doc, "", "", date(2026, 10, 16), rules).undated
['| 羊肉 (Lamb) | 0.5 份 | 9/20 | 冷藏/冷凍保存 |']
>>> unrecognized("### 2026-10-15\n晚餐：牛肉麵\n- **午餐**：水餃\n  - 配酸辣湯\n", date(2026, 10, 16), rules["recent_meal_days"])
['晚餐：牛肉麵']
```

## kitchen_inventory

Changes INVENTORY.md and logs meals and ideas in MEAL_LOGS.md, in one call. The examples edit this inventory:

```python
>>> from agents.meal_planner.lib.inventory_ops import KitchenItem, apply
>>> INVENTORY = "\n".join([
...     "### 肉類 (Meats)",
...     "| 食材名稱 | 剩餘份量 / 數量 | 入庫日期 | 預計保鮮期 / 備註 |", "| :--- | :--- | :--- | :--- |",
...     "| 豬排骨 (Pork ribs) | 1 盒 (2 份) | 2026-10-11 | 冷藏/冷凍保存 |",
...     "### 蛋品與乳製品 (Eggs & Dairy)",
...     "| 食材名稱 | 剩餘份量 / 數量 | 入庫日期 | 預計保鮮期 / 備註 |", "| :--- | :--- | :--- | :--- |",
...     "| 有機全脂鮮乳 (Whole milk) | 3 瓶 | 2026-10-13 | 冷藏保存 |",
...     "| 全脂鮮乳 (Whole milk) | 1 瓶 | 2026-09-26 | 冷藏保存 |",
...     "| 放牧雞蛋 (Eggs) | 24 顆 (2 打) | 2026-10-13 | 冷藏保存 |",
... ]) + "\n"
>>> def run(op, *items):
...     doc = InventoryDoc(INVENTORY)
...     return doc, apply(doc, op, [KitchenItem(**item) for item in items], date(2026, 10, 16), rules)
```

### Finding the row

A name matches a row's whole name, then any part of it (the Chinese, the English in parentheses, or an alternative split by `/`), then a piece of it at least 2 characters long. A piece of several different names is refused: use the exact name. Rows sharing a name are batches of one item, taken soonest-expiring first (then oldest 入庫日期, then file order).

### use

Deducts `portions` (default 1; a portion is one family meal) or `qty` in a unit the row counts. A row that reaches 0 is removed; a request bigger than the first batch spills into the next. A restating amount in parentheses scales along ("1 盒 (2 份)"); amounts joined by `+` are separate. Numbers are written with at most two decimals.

```python
>>> doc, result = run("use", {"name": "Pork ribs", "portions": 0.5})
>>> result.changed, result.consumed
([{'item': '豬排骨 (Pork ribs)', 'used': '0.5 份', 'left': '0.75 盒 (1.5 份)'}], ['豬排骨（消耗 0.5 份，剩餘 0.75 盒 (1.5 份)）'])
>>> doc, result = run("use", {"name": "Whole milk", "qty": "2 瓶"})
>>> [(entry["item"], entry["used"], entry.get("left", "removed")) for entry in result.changed]
[('全脂鮮乳 (Whole milk)', '1 瓶', 'removed'), ('有機全脂鮮乳 (Whole milk)', '1 瓶', '2 瓶')]
>>> run("use", {"name": "雞蛋", "qty": "4 顆"})[1].changed
[{'item': '放牧雞蛋 (Eggs)', 'used': '4 顆', 'left': '20 顆 (1.67 打)'}]
>>> run("use", {"name": "青花菜 (Broccoli)"})[1].errors
[('青花菜 (Broccoli)', 'not in the inventory')]
```

### add

Each purchase is a new row at the end of its section's table; a section the name doesn't find is created at the end of the file. 入庫日期 is `added`, default today. The amount is `qty`, `portions`, both ("1 盒 (2 份)"), or with neither, `default_portions` portions (default 2).

保鮮至 is written only when it's known: from `expires` (a date on the package or from the user), or from `shelf_life_days` (how long the user says it keeps, counted from 入庫日期). Otherwise no date is written, and the expiry is estimated from 入庫日期 each time the row is read, as in kitchen_snapshot.

```python
>>> doc, result = run("add", {"name": "雞腿 (Chicken thighs)", "section": "Meats", "note": "冷藏保存"})
>>> result.changed
[{'item': '雞腿 (Chicken thighs)', 'qty': '2 份', 'section': '肉類 (Meats)', 'days_left': 3, 'est': True}]
>>> [line for line in doc.render().splitlines() if "雞腿" in line]
['| 雞腿 (Chicken thighs) | 2 份 | 2026-10-16 | 冷藏保存 |']
>>> doc, result = run("add", {"name": "牛奶 (Milk)", "qty": "1 瓶", "section": "Dairy", "added": "2026-10-14", "expires": "2026-10-25"})
>>> [line for line in doc.render().splitlines() if "牛奶" in line]
['| 牛奶 (Milk) | 1 瓶 | 2026-10-14 | 保鮮至 2026-10-25 |']
```

### remove and set

`remove` deletes the first batch. `set` corrects a row's `qty`, `portions` (a restating amount scales along), 入庫日期 or note (a written 保鮮至 stays), or writes 保鮮至 from `expires` or `shelf_life_days`. A `set` with nothing to change is refused.

```python
>>> run("set", {"name": "豬排骨 (Pork ribs)", "portions": 1})[1].changed[0]["qty"]
'0.5 盒 (1 份)'
>>> run("remove", {"name": "Whole milk"})[1].changed
[{'item': '全脂鮮乳 (Whole milk)', 'removed': True, 'was': '1 瓶'}]
```

### Logging

`meal` goes under its date's heading (`days_ago` back from today), with a 消耗紀錄 line listing what `use` took in the same call. `ideas` go under today's 晚餐提案 heading, numbered after any already there. A day that has no heading yet gets one at the end of the file.

```python
>>> from agents.meal_planner.lib.meal_log import MealIdea, log_ideas, log_meal
>>> text = log_meal("# 膳食日誌 (Meal Logs)\n", date(2026, 10, 16), "晚餐", "紅燒排骨", ["豬排骨（消耗 0.5 份，剩餘 0.75 盒 (1.5 份)）"])
>>> print(log_ideas(text, date(2026, 10, 16), [MealIdea(theme="清爽海味", dishes=["香煎鮭魚", "蒜炒青菜"])]), end="")
# 膳食日誌 (Meal Logs)
<BLANKLINE>
### 2026-10-16
- **晚餐**：紅燒排骨
- **消耗紀錄**：豬排骨（消耗 0.5 份，剩餘 0.75 盒 (1.5 份)）
<BLANKLINE>
### 2026-10-16 晚餐提案 (Dinner Recommendations)
- **提案一（清爽海味）**：香煎鮭魚 ＋ 蒜炒青菜
```

### Writes, and what it couldn't do

- It checks permission for every file it will write before writing anything, and a missing INVENTORY.md or MEAL_LOGS.md fails the call. So a check-in is written whole or not at all.
- A file is rewritten under a lock, through a temp file; lines nobody touched stay byte for byte. If nothing changed, nothing is written.
- An item it can't apply (not found, ambiguous, the wrong unit) is an issue (`partial`), and the other items still apply. When an item isn't found, INVENTORY.md lines it can't read come back raw, in case the item is on one of them.

## dinner_checkin

The scan before the 21:00 dinner question. It reads MEAL_LOGS.md and skips the question when today's date, local time, already has an entry labeled 晚餐 (eating out included). Lunch, snacks and the day's 晚餐提案 don't count. A missing file, or lines in today's section it can't read, don't skip the question: they ride along as issues.

```python
>>> from agents.meal_planner.lib.meal_log import logged_meal
>>> logged_meal("### 2026-10-16\n- **午餐**：水餃\n- **晚餐**：外食（拉麵）\n", date(2026, 10, 16))
'晚餐：外食（拉麵）'
>>> logged_meal("### 2026-10-16 晚餐提案 (Dinner Recommendations)\n- **提案一**：香煎鮭魚\n", date(2026, 10, 16)) is None
True
```
