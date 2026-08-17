---
name: recommend_meals
description: Analyzes inventory and user context to generate 3 flexible, healthy dinner recommendations in Traditional Chinese.
---
## Overview
This skill generates 3 dinner suggestions by analyzing `pkm/wiki/kitchen/INVENTORY.md`, past suggestions in `pkm/wiki/kitchen/MEAL_LOGS.md`, and the user's learned palate in `CONTEXT.md` / `FEEDBACK.md`.

## Workflow

### 1. Read State
- Read `pkm/wiki/kitchen/INVENTORY.md` to see what needs to be used immediately.
- Read `pkm/wiki/kitchen/MEAL_LOGS.md` to avoid repeating recent meals.
- Ensure you have checked the agent's long-term `FEEDBACK.md` and `CONTEXT.md` to incorporate learned taste preferences and time constraints.

### 2. Generate 3 Suggestions
Develop 3 distinct meal options prioritizing items nearing expiration:
- **Variety**: Ensure they differ in flavor profile or cooking method.
- **Flexibility**: If a minor ingredient is missing from inventory, suggest the meal anyway. If a core ingredient is missing, ensure it's a dish that can easily substitute it or relies mostly on what's available.
- **Health & Ease**: Focus on healthy, Asian-leaning dishes (unless they request otherwise) that are relatively easy to prepare after a workday.

### 3. Output Requirements
Present the 3 options to the user.
- **Language**: Dish names, required ingredients, and cooking instructions MUST be in Traditional Chinese. 
- **Format**: Keep instructions clear and actionable. Do not provide English translations for the recipes, though introductory/transitional text can be in English.

### 4. Log the Suggestions
- Write the suggested meals (with date) to `pkm/wiki/kitchen/MEAL_LOGS.md` so the system knows what was proposed today.

## Required Tools
- `filesystem`: Required to read inventory/logs and append suggestions to `pkm/wiki/kitchen/MEAL_LOGS.md`, and read `agents/meal-planner/` for context.