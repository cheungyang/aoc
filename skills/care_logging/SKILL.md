---
name: care_logging
description: Handles logging qualitative milestones and quantitative health data for Aila, distinguishing between silent background routing and active coaching.
---

## Core

Use this skill when processing updates about Aila's health, development, or daily milestones.

### Step 1: Determine the Mode
- **Routed Mode (Silent Bookkeeper)**: If you receive a handoff brief from another agent, you are receiving background data. Extract the data, format it, and append it to the appropriate file silently. Do not ask follow-up questions. Provide a very brief response confirming the silent log.
- **Direct Mode (Active Coach)**: If the user is speaking to you directly, warmly acknowledge the update. Use the aila_dob threshold in RULES.md to consider her exact age. If the information lacks context (e.g., severity of a rash, emotional context of a milestone), ask a clarifying question. Once complete, append the data and confirm it gently.

### Step 2: Extract and Append Data
- **Health Data**: Route weight, height, vaccinations, and symptoms to `health_log.md`.
- **Milestones**: Route qualitative achievements and firsts to `milestones.md`.
- Use `append` to add the new timestamped entry to the end of the appropriate file.

Call load_skill with care_logging for the Reference section with tool requirements.

## Reference

### Required Tools
- `filesystem`: To `read` and `append` to `pkm/vault/wallets/ayla/health_log.md` and `milestones.md`.
