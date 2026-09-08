---
name: manage_kitchen_inventory
description: Translates English grocery inputs and receipts into Traditional Chinese and smartly deduces/deducts ingredients for cooked meals in INVENTORY.md.
---
## Overview
This skill reads and updates the `INVENTORY.md` and `STAPLES.md` files in `pkm/wallet/kitchen/`. It handles three primary workflows: processing scanned receipt images, adding text-based grocery items, and deducting used ingredients after a cooked meal is reported.

## Workflows

### 1. Processing Receipt Images
- **Input**: The user provides a receipt (either uploaded directly in chat or passed as a local path for `read_image`).
- **Action**: Use your native multimodal vision to scan the receipt image. Extract all food and grocery items, actively ignoring non-food purchases.
- **Translation**: Translate all extracted food items into Traditional Chinese.
- **Portions & Dates**: Assume items cover 2 meal portions by default (unless evident otherwise). Calculate the `Added Date` (today) and `Est. Expiration` based on perishable type.
- **Update**: Append the items to the markdown table in `pkm/wallet/kitchen/INVENTORY.md`.

### 2. Adding Groceries (Text)
- **Input**: The user lists newly bought items via text (often in English).
- **Action**: Translate all items to Traditional Chinese.
- **Portions**: Assume items cover 2 meal portions by default, unless the user specifies otherwise.
- **Dates**: Calculate the `Added Date` (today) and `Est. Expiration` based on the perishable type (e.g., meat vs. hardy vegetables).
- **Update**: Append the items to the markdown table in `pkm/wallet/kitchen/INVENTORY.md`.

### 3. Deducting Cooked Meals
- **Input**: The user reports what they cooked (e.g., "Beef and Broccoli").
- **Action**: Smartly deduce the core ingredients used for that dish without making the user list everything out.
- **Deduction**: Decrease the portions of those deduced ingredients by 1 meal portion (since 1 meal covers the family). 
- **Removal**: If an item's portion count hits 0 or below, remove it entirely from the table.
- **Update**: Write the updated table back to `pkm/wallet/kitchen/INVENTORY.md` and log the cooked meal in `pkm/wallet/kitchen/MEAL_LOGS.md`.

## Boundaries
- All data written to the kitchen files MUST be in Traditional Chinese.
- Do not ask the user for granular ingredient lists for standard dishes. Trust your smart deduction based on their dish name.

## Required Tools
- `filesystem`: Required to read, overwrite, append, and read_image from `pkm/wallet/kitchen/INVENTORY.md`, `pkm/wallet/kitchen/STAPLES.md`, and `pkm/wallet/kitchen/MEAL_LOGS.md`.