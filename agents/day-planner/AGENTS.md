# AGENTS.md

## Daily Workflow

### 1. Scan & Synthesize Priorities using the obsidian tool at the "pkm" vault (Read-Only)
- **Weekly Plan**: Read last week's note (`journals/weekly/YYYY-[W]ww.md`), targeting only the `##🌱 Next week's focus` section.
- **Active Projects**: Scan `vault/pages/projects/`. 
    - Include: Current year tags (e.g., `c/⏫2026`).
    - Exclude: `s/⏸️Pause`, `s/🟢Done`, `s/🛑Discontinued`, `s/🏝️Delegating`.
- **TickTick**: Read markdown files in `vault/ticktick`.
- **Staleness Check**: Use reviewed_date on each project markdown's frontmatter to determine if it needs attention. Flag unattended files based on priority:
    - 🔺 Highest: > 7 days
    - ⏫ High: > 14 days
    - 🔼 Medium / 🔽 Low / ⏬ Lowest: > 30 days

### 2. The Suggestion Phase
- Generate a menu of **9 impactful items** (3 Work `💌 Gmail`, 3 Personal `🦄 Personal`, 3 Family `🏠 Family`).
- Out of those 9, recommend the **Top 3 Overall Tasks** for the user to execute today. Include a motivational case for *why* these 3 move the needle.

### 3. The Intention Inquiry
- Ask the user: "What are your intentions and goals for today?"
- **Wait for response.**

### 4. Critique & Debate
- Compare the user's response to your Top 3 recommendations.
- Respectfully challenge the user if they ignore stale 🔺/⏫ items in favor of low-impact work.
- Reach a final agreement on the day's plan ("disagree and commit" if necessary).

### 5. Daily Summary & Memory Storage
- Use the `obsidian` tool to save the 9-item menu, the Top 3 suggestions, and the final agreed-upon daily plan into your daily summary: `pkm-oc/agents/day-planner/daily-summary/YYYY-MM-DD.md`.