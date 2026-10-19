---
name: manage_kitchen_inventory
description: Records groceries, receipts and cooked meals in the kitchen inventory, in Traditional Chinese, deducing a dish's ingredients from its name.
---
## Workflows

### Groceries and receipts
- Read receipt images with your own vision (uploaded in chat, or a local path via `filesystem` `read_image`). Take every food and grocery item; ignore non-food purchases.
- Translate each item into Traditional Chinese.
- Add them all in one `kitchen_inventory` call (`op="add"`), each with its section and storage note (冷藏保存, 冷凍保存, 常溫保存: the expiry estimate reads it). A receipt's date is `added`.
- Give `expires` only for a date printed on the package or given by the user, and `shelf_life_days` only when the user says how long it keeps. Never guess either: without them, the expiry is estimated from the purchase date.

### A meal the user cooked
- Deduce the core ingredients from the dish name; don't make the user list them.
- Match them to the names in `kitchen_snapshot` (call it first if you don't have it this turn).
- Deduct and log in one `kitchen_inventory` call: `op="use"` with those items and `meal` set to the dish. Skip ingredients the inventory doesn't have.
- Amounts: one 份 is one family meal. `use` takes 1 份 unless you give `portions` (fractions like 0.5 are fine) or `qty` in the row's own unit (e.g. '4 顆' eggs).

### Expiry dates
- Work from `kitchen_snapshot`'s days left; don't recompute them. Days marked `est` are estimates from the purchase date: say "about".
- Rows past their date: ask whether they're gone, and `remove` only the ones the user confirms.
- A date for one item is `op="set"` with `expires`. A general rule ("strawberries keep 3 days") is a `shelf_life_days` entry in RULES.md: propose it (old → new) and save it once the user agrees.

### Corrections and staples
- Finished, spoiled or missing items: `op="remove"`. Wrong amounts, dates or notes: `op="set"`.
- An item `kitchen_inventory` couldn't apply comes back under issues; retry with the exact name from `kitchen_snapshot`.
- Staples are the basics in `pkm/wallet/kitchen/STAPLES.md`, assumed always stocked. When the user adds or drops one, edit that file with `filesystem` (`append`, `replace_block`).

## Boundaries
- Everything written to the kitchen files is in Traditional Chinese.
- Don't ask for ingredient lists for standard dishes; trust your deduction from the dish name.