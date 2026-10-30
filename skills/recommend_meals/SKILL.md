---
name: recommend_meals
description: Suggests 3 flexible, healthy dinner ideas in Traditional Chinese from what's in the kitchen, what the family ate lately and their learned palate.
---
## Workflow
1. Call `kitchen_snapshot`, asking for `medium` thinking: choosing the dinners is the heavy step. Take the family's tastes and time limits from your prompt (profile, shared topics, feedback); don't re-read memory files.
2. Choose 3 dinners, using first what expires first (the snapshot's days left, already computed; `est` ones are rough estimates):
   - **Variety**: they differ in flavor profile or cooking method, and from recent meals and the last ideas.
   - **Flexibility**: suggest a dish even if a minor ingredient is missing. If a core ingredient is missing, pick a dish that substitutes it easily or relies mostly on what's there. A missing ingredient rules a dish out only when the user says it's a blocker. Staples are always there.
   - **Health and ease**: healthy, Asian-leaning (unless they ask otherwise), easy to make after a workday.
3. Log them as `ideas` in a `kitchen_inventory` call (`op="log"`, or the `use` call when you're also logging a meal), then reply.
4. If items are past their date, name them in one line and ask whether they're gone; `remove` the ones the user confirms.

## Output
- Dish names, ingredients and cooking instructions in Traditional Chinese, with no English translations of the recipes; the text around them can be English.
- Keep instructions clear and actionable.