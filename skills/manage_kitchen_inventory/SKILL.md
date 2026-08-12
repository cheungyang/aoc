---
name: manage_kitchen_inventory
description: Translates English grocery inputs into Traditional Chinese and smartly deduces/deducts ingredients for cooked meals in INVENTORY.md.
---
## Overview
This skill reads and updates the `INVENTORY.md` and `STAPLES.md` files in `pkm/wiki/kitchen/`. It handles two primary workflows: adding new grocery items and deducting used ingredients after a cooked meal is reported.

## Workflows

### 1. Adding Groceries
- **Input**: The user lists newly bought items (often in English).
- **Action**: Translate all items to Traditional Chinese.
- **Portions**: Assume items cover 2 meal portions by default, unless the user specifies otherwise.
- **Dates**: Calculate the `Added Date` (today) and `Est. Expiration` based on the perishable type (e.g., meat vs. hardy vegetables).
- **Update**: Append the items to the markdown table in `pkm/wiki/kitchen/INVENTORY.md`.

### 2. Deducting Cooked Meals
- **Input**: The user reports what they cooked (e.g., "Beef and Broccoli").
- **Action**: Smartly deduce the core ingredients used for that dish without making the user list everything out.
- **Deduction**: Decrease the portions of those deduced ingredients by 1 meal portion (since 1 meal covers the family). 
- **Removal**: If an item's portion count hits 0 or below, remove it entirely from the table.
- **Update**: Write the updated table back to `pkm/wiki/kitchen/INVENTORY.md` and log the cooked meal in `pkm/wiki/kitchen/MEAL_LOGS.md`.

## Boundaries
- All data written to the kitchen files MUST be in Traditional Chinese.
- Do not ask the user for granular ingredient lists for standard dishes. Trust your smart deduction based on their dish name.

## Required Tools
- `obsidian`: Required to read and overwrite `wiki/kitchen/INVENTORY.md`, `wiki/kitchen/STAPLES.md`, and `wiki/kitchen/MEAL_LOGS.md`.